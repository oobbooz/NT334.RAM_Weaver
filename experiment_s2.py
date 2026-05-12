#!/usr/bin/env python
"""experiment_s2.py — Tái hiện S2: Single Message Restoration Accuracy (Table 2).

Với mỗi message trong Amazon Reviews dataset:
  1. Đọc AMC chunk file tương ứng (output của send_and_dump.py → AMC)
  2. Gửi chunk cho LLM restore
  3. So sánh kết quả với ground truth (= text review gốc)
  4. Tính EMR và CER tổng hợp như Table 2 trong paper

Chuẩn bị trước khi chạy:
  - Chạy send_and_dump.py trên Windows VM để có dumps/ và messages.txt
  - Hoặc chạy AMC trước để có chunks/

Chế độ A — Đã có sẵn chunk files:
    chunks/msg_0001.txt  ← AMC output của message 1
    chunks/msg_0002.txt
    ...
    messages.txt         ← mỗi dòng là 1 review text gốc (thứ tự khớp chunks/)

    python experiment_s2.py --chunks-dir chunks/ --gt-file messages.txt

Chế độ B — Có dump files, chạy AMC trực tiếp:
    dumps/msg_0001.raw   ← dump RAM sau khi gửi message 1
    dumps/msg_0002.raw
    ...
    messages.txt

    python experiment_s2.py --dumps-dir dumps/ --pid 6864 --gt-file messages.txt

Options:
    --limit 10           Chỉ chạy 10 message đầu (test nhanh)
    --throttle 1.0       Delay giữa các API call (giây)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for p in [str(ROOT), str(ROOT / "llm"), str(ROOT / "pipeline")]:
    if p not in sys.path:
        sys.path.insert(0, p)

_env = ROOT / ".env"
if _env.is_file():
    for line in _env.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

# ── Metrics — import thẳng từ llm/metrics.py ─────────────────────────────────
from llm.metrics import character_error_rate as cer, evaluate


# ── Load dữ liệu ──────────────────────────────────────────────────────────────

def load_chunks_from_dir(chunks_dir: str, limit: int | None = None) -> list[str]:
    """Đọc tất cả .txt file trong chunks_dir, sắp xếp theo tên."""
    files = sorted(Path(chunks_dir).glob("*.txt"))
    if limit:
        files = files[:limit]
    chunks = []
    for f in files:
        chunks.append(f.read_text(encoding="utf-8"))
    print(f"  Đọc {len(chunks)} chunk files từ {chunks_dir}")
    return chunks


def load_ground_truth(gt_file: str, limit: int | None = None) -> list[str]:
    """Đọc ground truth file, mỗi dòng là 1 message."""
    lines = Path(gt_file).read_text(encoding="utf-8").strip().splitlines()
    lines = [l.strip() for l in lines if l.strip()]
    if limit:
        lines = lines[:limit]
    print(f"  Đọc {len(lines)} ground truth messages từ {gt_file}")
    return lines


def run_amc_on_dump(dump_path: str, pid: int) -> str:
    """Chạy full AMC pipeline trên một dump file, trả về chunk text."""
    from config import AMCConfig
    from amc.pipeline import AdaptiveMemoryCarver

    cfg = AMCConfig()
    amc = AdaptiveMemoryCarver(cfg)
    output_path = amc.run(dump_path, pid, output_name=f"s2_{Path(dump_path).stem}.txt")
    if not output_path or not os.path.isfile(output_path):
        return ""
    return Path(output_path).read_text(encoding="utf-8")


def extract_new_message_only(
    current_amc_output: str,
    previous_amc_output: str | None = None,
) -> str:
    """Extract chỉ message MỚI từ dump hiện tại.
    
    So sánh với dump trước, bỏ đi messages cũ đã có.
    Lấy chỉ content mới xuất hiện.
    """
    if not previous_amc_output:
        # Lần đầu tiên: trả về toàn bộ
        return current_amc_output
    
    # Split by ---CHUNK--- separator (nếu có)
    curr_chunks = current_amc_output.split("---CHUNK---")
    prev_chunks = previous_amc_output.split("---CHUNK---")
    
    # Tìm chunks mới (không có trong previous)
    new_chunks = []
    for chunk in curr_chunks:
        chunk_clean = chunk.strip()
        if not chunk_clean:
            continue
        # Nếu chunk này không tồn tại trong previous, thêm vào
        if chunk_clean not in previous_amc_output:
            new_chunks.append(chunk_clean)
    
    # Nếu không tìm được chunk mới qua exact match, 
    # lấy chunk cuối cùng từ current (có thể là message mới)
    if not new_chunks and curr_chunks:
        # Lấy chunks cuối cùng từ current
        last_chunk = curr_chunks[-1].strip()
        if last_chunk and last_chunk not in previous_amc_output:
            new_chunks.append(last_chunk)
    
    # Nếu vẫn không tìm được, trả về toàn bộ current
    # (fallback - có thể message mới được merged với messages cũ)
    if not new_chunks:
        return current_amc_output
    
    return "---CHUNK---".join(new_chunks)


# ── LLM restore ───────────────────────────────────────────────────────────────

def make_llm_caller():
    """Tạo hàm gọi LLM, trả về callable(fragment) → restored_text."""
    try:
        from config import LLMConfig
        from llm.client import create_client
        from llm.prompts import RESTORE_SYSTEM_PROMPT, RESTORE_USER_TEMPLATE
    except ImportError as e:
        print(f"[ERROR] Không import được LLM modules: {e}")
        sys.exit(1)

    cfg = LLMConfig()
    llm = create_client(cfg)
    print(f"  LLM: {cfg.provider} / {cfg.model}")

    def call(fragment: str) -> str:
        user_msg = RESTORE_USER_TEMPLATE.format(fragment=fragment[:cfg.max_input_chars])
        result = llm.generate(RESTORE_SYSTEM_PROMPT, user_msg)
        return (result or "").strip()

    return call


# ── Chạy experiment ───────────────────────────────────────────────────────────

def extract_message_text(llm_output: str) -> str:
    """Extract chỉ message text từ LLM output.
    
    LLM trả về format: [HH:MM:SS] <uid>: <message_text>
    Nhưng ground truth chỉ có <message_text>
    
    Function này loại bỏ timestamp và UID để so sánh chỉ message text.
    """
    text = llm_output.strip()
    
    # Pattern: [HH:MM:SS] <uid>: <message>
    # Tìm ": " và lấy phần sau nó
    if "]: " in text:
        # Format: [14:16:25] u812...: message
        parts = text.split("]: ", 1)
        if len(parts) == 2:
            msg_part = parts[1]
            # Loại bỏ UID trước :
            if ": " in msg_part:
                msg_text = msg_part.split(": ", 1)[1]
                return msg_text
            return msg_part
    elif ": " in text:
        # Fallback: chỉ có <uid>: <message>
        return text.split(": ", 1)[1]
    
    return text


def run_experiment(
    chunks: list[str],
    ground_truths: list[str],
    llm_caller,
    throttle: float = 0.5,
) -> list[dict]:
    """Chạy restore từng chunk, đo EMR/CER."""
    if len(chunks) != len(ground_truths):
        n = min(len(chunks), len(ground_truths))
        print(f"  ⚠ Số chunk ({len(chunks)}) ≠ số ground truth ({len(ground_truths)}). Dùng {n} cái đầu.")
        chunks = chunks[:n]
        ground_truths = ground_truths[:n]

    results = []
    total = len(chunks)

    for i, (chunk, ref) in enumerate(zip(chunks, ground_truths), 1):
        print(f"  [{i:>3}/{total}] ", end="", flush=True)
        t0 = time.time()
        try:
            hyp_raw = llm_caller(chunk)
            # Extract chỉ message text, loại bỏ timestamp + UID
            hyp = extract_message_text(hyp_raw)
            elapsed = time.time() - t0
            exact = hyp == ref
            cer_score = cer(ref, hyp)
            mark = "✓" if exact else "✗"
            print(f"{mark}  CER={cer_score:.3f}  ({elapsed:.1f}s)")
            if not exact:
                ref_short = ref[:60] + ("..." if len(ref) > 60 else "")
                hyp_short = hyp[:60] + ("..." if len(hyp) > 60 else "")
                print(f"         Ref: {repr(ref_short)}")
                print(f"         Got: {repr(hyp_short)}")
            results.append({
                "idx": i,
                "reference": ref,
                "hypothesis": hyp,
                "exact": exact,
                "cer_score": cer_score,
                "elapsed": elapsed,
            })
        except Exception as exc:
            print(f"✗  LỖI: {exc}")
            results.append({
                "idx": i,
                "reference": ref,
                "hypothesis": "",
                "exact": False,
                "cer_score": float("inf"),
                "elapsed": 0.0,
            })

        if i < total:
            time.sleep(throttle)

    return results


# ── In bảng kết quả ───────────────────────────────────────────────────────────

def print_results(results: list[dict], model_name: str) -> None:
    n = len(results)
    if n == 0:
        print("Không có kết quả.")
        return

    refs = [r["reference"] for r in results]
    hyps = [r["hypothesis"] for r in results]
    metrics = evaluate(refs, hyps)
    emr = metrics["emr"]
    avg_cer = metrics["avg_cer"]
    exact_count = int(emr * n)
    total_time = sum(r["elapsed"] for r in results)

    print()
    print("=" * 70)
    print("KẾT QUẢ S2 — Single Message Restoration Accuracy")
    print("=" * 70)
    print(f"  Model    : {model_name}")
    print(f"  Messages : {n}")
    print(f"  EMR      : {emr*100:.1f}%  ({exact_count}/{n} exact match)")
    print(f"  Avg CER  : {avg_cer:.2f}")
    print(f"  Tổng time: {total_time:.1f}s  (avg {total_time/n:.1f}s/msg)")
    print(f"  {model_name[:18]:<18} {emr*100:.0f}%  ← {avg_cer:.2f}")
    print("=" * 70)


def save_results(results: list[dict], model_name: str) -> None:
    os.makedirs("./output", exist_ok=True)
    out_path = f"./output/s2_result_{model_name.replace('/', '_').replace(':', '_')}.txt"
    # Compute summary metrics
    refs = [r["reference"] for r in results]
    hyps = [r["hypothesis"] for r in results]
    metrics = evaluate(refs, hyps)
    emr = metrics.get("emr", 0.0)
    avg_cer = metrics.get("avg_cer", 0.0)
    total_time = sum(r.get("elapsed", 0.0) for r in results)
    n = len(results)

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(f"S2 Restoration Accuracy\nModel: {model_name}\n\n")
        fh.write(f"Messages: {n}\n")
        fh.write(f"EMR: {emr*100:.2f}% ({int(emr*n)}/{n})\n")
        fh.write(f"Avg CER: {avg_cer:.4f}\n")
        fh.write(f"Total time (s): {total_time:.2f}  (avg {total_time/n:.2f}s/msg)\n\n")

        fh.write("Detailed per-message results:\n\n")
        for r in results:
            status = "EXACT" if r["exact"] else f"CER={r['cer_score']:.3f}"
            fh.write(f"[{r['idx']:03d}] {status}\n")
            fh.write(f"  Ref: {r['reference']}\n")
            if not r["exact"]:
                fh.write(f"  Got: {r['hypothesis']}\n")
            fh.write("\n")
    print(f"\nKết quả chi tiết: {out_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="S2: Single Message Restoration Accuracy")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--chunks-dir", help="Thư mục chứa chunk .txt files (1 file/message)")
    mode.add_argument("--dumps-dir", help="Thư mục chứa .raw/.dmp files (1 dump/message)")

    parser.add_argument("--gt-file", help="File ground truth (1 dòng/message)")
    parser.add_argument("--pid", type=int, help="PID LINE Messenger (dùng với --dumps-dir)")
    parser.add_argument("--limit", type=int, help="Giới hạn số message (để test nhanh)")
    parser.add_argument("--throttle", type=float, default=0.5,
                        help="Delay giữa các API call (giây, mặc định 0.5)")
    args = parser.parse_args()

    print("=" * 70)
    print("RAM-Weaver — S2: Single Message Restoration Accuracy")
    print("=" * 70)

    # ── Load dữ liệu ──────────────────────────────────────────────────────────
    if args.chunks_dir:
        if not args.gt_file:
            print("[ERROR] Cần --gt-file khi dùng --chunks-dir")
            sys.exit(1)
        print(f"\n[Chế độ A] Chunk files từ {args.chunks_dir}")
        chunks = load_chunks_from_dir(args.chunks_dir, limit=args.limit)
        ground_truths = load_ground_truth(args.gt_file, limit=args.limit)

    else:  # --dumps-dir
        if not args.gt_file or not args.pid:
            print("[ERROR] Cần --gt-file và --pid khi dùng --dumps-dir")
            sys.exit(1)
        print(f"\n[Chế độ B] Chạy AMC trên từng dump trong {args.dumps_dir}")

        # Hỗ trợ cả .raw (winpmem) và .dmp (procdump)
        dump_files = sorted(Path(args.dumps_dir).glob("*.raw"))
        if not dump_files:
            dump_files = sorted(Path(args.dumps_dir).glob("*.dmp"))
        if args.limit:
            dump_files = dump_files[:args.limit]

        ground_truths = load_ground_truth(args.gt_file, limit=args.limit)

        print(f"  Chạy AMC trên {len(dump_files)} dump files...")
        chunks = []
        prev_amc_output = None  # Để track dump trước, lấy chỉ message mới
        for df in dump_files:
            print(f"    AMC: {df.name}...", end=" ", flush=True)
            chunk = run_amc_on_dump(str(df), args.pid)
            if chunk:
                # Extract chỉ message MỚI (DIFF với dump trước)
                new_chunk = extract_new_message_only(chunk, prev_amc_output)
                prev_amc_output = chunk  # Save cho lần sau
                print(f"OK ({len(chunk)/1024:.1f} KB) → {len(new_chunk)/1024:.1f} KB (mới)")
                chunks.append(new_chunk)
            else:
                print("FAIL (chunk rỗng)")
                chunks.append("")
                prev_amc_output = chunk

    if not chunks:
        print("[ERROR] Không có dữ liệu để chạy.")
        sys.exit(1)

    # ── Tạo LLM caller ────────────────────────────────────────────────────────
    print("\nKhởi tạo LLM client...")
    llm_caller = make_llm_caller()

    provider = os.environ.get("RAM_WEAVER_LLM_PROVIDER", "gemini")
    model = os.environ.get("RAM_WEAVER_LLM_MODEL") or os.environ.get("RAM_WEAVER_GEMINI_MODEL", "gemini-2.5-flash")
    model_name = f"{provider}/{model}"

    # ── Chạy experiment ───────────────────────────────────────────────────────
    print(f"\nChạy restore {len(chunks)} messages...\n")
    results = run_experiment(chunks, ground_truths, llm_caller, throttle=args.throttle)

    # ── Kết quả ───────────────────────────────────────────────────────────────
    print_results(results, model_name)
    save_results(results, model_name)


if __name__ == "__main__":
    main()