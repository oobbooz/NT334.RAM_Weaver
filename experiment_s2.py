#!/usr/bin/env python
"""experiment_s2.py — Tái hiện S2: Single Message Restoration Accuracy (Table 2).

Với mỗi message trong Amazon Reviews dataset:
  1. Đọc AMC chunk file tương ứng (output của send_and_dump.py → AMC)
  2. Gửi chunk cho LLM restore
  3. So sánh kết quả với ground truth (= text review gốc)
  4. Tính EMR và CER tổng hợp như Table 2 trong paper

Chuẩn bị trước khi chạy:
  - Chạy send_and_dump.py trên Windows VM để có dumps/ và ground_truth.txt
    - AMC sẽ lưu output theo message vào output/amc/ (vd: s2_msg_0001.txt, ...)

Chế độ A — Đã có sẵn AMC output files (khuyến nghị):
    output/amc/s2_msg_0001.txt  ← AMC output của message 1
    output/amc/s2_msg_0002.txt
    ...
    ground_truth.txt            ← mỗi dòng là 1 review text gốc (thứ tự khớp các file trên)

    python experiment_s2.py --chunks-dir output/amc --gt-file ground_truth.txt

Chế độ B — Có dump files, chạy AMC trực tiếp (sẽ reuse cache nếu đã có):
    dumps/msg_0001.raw   ← dump RAM sau khi gửi message 1
    dumps/msg_0002.raw
    ...
   ground_truth.txt

    python experiment_s2.py --dumps-dir dumps/ --pid 8092 --gt-file ground_truth.txt

    Ghi chú: nếu file `s2_<dump_stem>.txt` đã tồn tại (thường ở output/amc/)
    thì script sẽ đọc lại file đó và bỏ qua bước chạy AMC tương ứng.

Options:
    --limit 10           Chỉ chạy 10 message đầu (test nhanh)
    --throttle 1.0       Delay giữa các API call (giây)
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for p in [str(ROOT), str(ROOT / "llm"), str(ROOT / "pipeline")]:
    if p not in sys.path:
        sys.path.insert(0, p)

# Load .env (project root) – không override biến môi trường đã set
from config import load_env
load_env(ROOT / ".env")

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

# ── Metrics — import thẳng từ llm/metrics.py ─────────────────────────────────
from llm.metrics import character_error_rate as cer, evaluate


# ── Load dữ liệu ──────────────────────────────────────────────────────────────

def list_chunk_files(chunks_dir: str, limit: int | None = None) -> list[Path]:
    """List .txt files trong chunks_dir, sắp xếp theo tên."""
    base = Path(chunks_dir)
    preferred = sorted(base.glob("s2_msg_*.txt"))
    if preferred:
        files = preferred
    else:
        alt = sorted(base.glob("msg_*.txt"))
        files = alt if alt else sorted(base.glob("*.txt"))
    if limit:
        files = files[:limit]
    return files


def load_chunks_from_dir(chunks_dir: str, limit: int | None = None) -> list[str]:
    """Đọc tất cả .txt file trong chunks_dir, sắp xếp theo tên."""
    files = list_chunk_files(chunks_dir, limit=limit)
    chunks = [f.read_text(encoding="utf-8") for f in files]
    print(f"  Đọc {len(chunks)} AMC output files từ {chunks_dir}")
    return chunks


def load_ground_truth(gt_file: str, limit: int | None = None) -> list[str]:
    """Đọc ground truth file, mỗi dòng là 1 message."""
    lines = Path(gt_file).read_text(encoding="utf-8").strip().splitlines()
    lines = [l.strip() for l in lines if l.strip()]
    if limit:
        lines = lines[:limit]
    print(f"  Đọc {len(lines)} ground truth từ {gt_file}")
    return lines


def _find_cached_amc_output(output_name: str) -> Path | None:
    """Tìm file AMC output đã có sẵn.

    Ưu tiên thư mục cấu hình qua RAM_WEAVER_OUTPUT_DIR (AMCConfig.output_dir),
    fallback về ./output/amc (layout thường gặp trong repo này).
    """
    try:
        from config import AMCConfig
    except Exception:
        AMCConfig = None  # type: ignore[assignment]

    candidate_dirs: list[Path] = []
    if AMCConfig is not None:
        try:
            cfg = AMCConfig()
            candidate_dirs.append(Path(cfg.output_dir))
        except Exception:
            pass

    # Fallbacks phổ biến trong workspace này
    candidate_dirs.extend(
        [
            ROOT / "output" / "amc",
            ROOT / "output",
        ]
    )

    for base in candidate_dirs:
        try:
            p = base / output_name
            if p.is_file():
                return p
        except Exception:
            continue
    return None


def run_amc_on_dump(dump_path: str, pid: int) -> tuple[str, bool, str]:
    """Chạy đầy đủ quy trình AMC trên một file dump.

    Trả về bộ 3: (chunk_text, reused_cache, path).
    """
    from config import AMCConfig
    from amc.pipeline import AdaptiveMemoryCarver

    output_name = f"s2_{Path(dump_path).stem}.txt"
    cached = _find_cached_amc_output(output_name)
    if cached is not None:
        try:
            return cached.read_text(encoding="utf-8", errors="ignore"), True, str(cached)
        except Exception:
            # Nếu đọc lỗi thì fallback chạy lại AMC
            pass

    cfg = AMCConfig()
    amc = AdaptiveMemoryCarver(cfg)
    output_path = amc.run(dump_path, pid, output_name=output_name)
    if not output_path or not os.path.isfile(output_path):
        return "", False, output_path or ""
    return Path(output_path).read_text(encoding="utf-8", errors="ignore"), False, output_path


def extract_new_message_only(
    current_amc_output: str,
    previous_amc_output: str | None = None,
) -> str:
    """Extract chỉ message MỚI từ dump hiện tại."""
    if not previous_amc_output:
        return current_amc_output
    
    curr_chunks = current_amc_output.split("---CHUNK---")
    prev_chunks = previous_amc_output.split("---CHUNK---")
    
    new_chunks = []
    for chunk in curr_chunks:
        chunk_clean = chunk.strip()
        if not chunk_clean:
            continue
        if chunk_clean not in previous_amc_output:
            new_chunks.append(chunk_clean)
    
    if not new_chunks and curr_chunks:
        last_chunk = curr_chunks[-1].strip()
        if last_chunk and last_chunk not in previous_amc_output:
            new_chunks.append(last_chunk)
    
    if not new_chunks:
        return current_amc_output
    
    return "---CHUNK---".join(new_chunks)


# ── LLM restore ───────────────────────────────────────────────────────────────

def make_llm_caller():
    """Tạo hàm gọi LLM, trả về callable(fragment) → restored_text."""
    try:
        from config import LLMConfig
        from llm.client import create_client
        from llm.prompts import RESTORE_S2_SINGLE_MSG_PROMPT, RESTORE_USER_TEMPLATE
    except ImportError as e:
        print(f"[ERROR] Không import được LLM modules: {e}")
        sys.exit(1)

    cfg = LLMConfig()
    llm = create_client(cfg)
    print(f"  LLM: {cfg.provider} / {cfg.model}")

    def call(fragment: str) -> str:
        user_msg = RESTORE_USER_TEMPLATE.format(fragment=fragment[:cfg.max_input_chars])
        result = llm.generate(RESTORE_S2_SINGLE_MSG_PROMPT, user_msg)
        return (result or "").strip()

    return call

def safe_llm_call(llm_caller, chunk, max_retries=10, throttle=0.5):
    """
    Hàm bọc gọi API an toàn, xử lý Rate Limit, Quota và cả Server Overload (503).
    """
    import time
    for attempt in range(max_retries):
        try:
            result = llm_caller(chunk)
            
            time.sleep(max(5, throttle)) 
            
            return result
            
        except Exception as e:
            error_msg = str(e).lower()
            
            if any(k in error_msg for k in ["429", "quota", "rate limit", "exhausted", "503", "unavailable", "high demand", "500"]):
                wait_time = 15 * (2 ** attempt) 
                print(f"\n    [!] API đang bận/quá tải (Lỗi 50x/429). Tự động chờ {wait_time}s rồi thử lại (Lần {attempt+1}/{max_retries})...")
                time.sleep(wait_time)
            else:
                raise e
                
    print("\n    [!] Bỏ qua do API lỗi liên tục quá nhiều lần.")
    return ""


# ── Chạy experiment ───────────────────────────────────────────────────────────

def extract_message_text(llm_output: str) -> str:
    """Extract chỉ message text từ LLM output."""
    text = llm_output.strip()
    if not text:
        return ""

    uid_re = r"(?:u[a-f0-9]{8,}|user_id_placeholder|[A-Za-z0-9_]{10,})"
    ts_only_re = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]$")
    uid_only_re = re.compile(rf"^{uid_re}:?$")
    ts_uid_inline_re = re.compile(rf"^\[\d{{2}}:\d{{2}}:\d{{2}}\]\s*{uid_re}:\s*")
    ts_inline_re = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s*")
    uid_inline_re = re.compile(rf"^{uid_re}:\s*")

    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if ts_only_re.match(line) or uid_only_re.match(line):
            continue

        line = ts_uid_inline_re.sub("", line)
        line = ts_inline_re.sub("", line)
        line = uid_inline_re.sub("", line)

        if line:
            cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


def run_experiment(
    chunks: list[str],
    ground_truths: list[str],
    llm_caller,
    throttle: float = 0.5,
    labels: list[str] | None = None,
    restore_dir: str = "./output/restore",
) -> list[dict]:
    """Chạy restore từng chunk, đo EMR/CER."""
    if len(chunks) != len(ground_truths):
        n = min(len(chunks), len(ground_truths))
        print(f"  ⚠ Số chunk ({len(chunks)}) ≠ số ground truth ({len(ground_truths)}). Dùng {n} cái đầu.")
        chunks = chunks[:n]
        ground_truths = ground_truths[:n]

    def _safe_label(label: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("._-") or "item"

    os.makedirs(restore_dir, exist_ok=True)

    results = []
    total = len(chunks)

    for i, (chunk, ref) in enumerate(zip(chunks, ground_truths), 1):
        print(f"  [{i:>3}/{total}] ", end="", flush=True)
        t0 = time.time()
        label = None
        if labels and i <= len(labels):
            label = _safe_label(labels[i - 1])
        else:
            label = f"restored_{i:04d}"

        try:
            # SỬ DỤNG SAFE_LLM_CALL Ở ĐÂY THAY VÌ GỌI TRỰC TIẾP
            hyp_raw = safe_llm_call(llm_caller, chunk, throttle=throttle)
            
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
                "raw_output": hyp_raw,
                "exact": exact,
                "cer_score": cer_score,
                "elapsed": elapsed,
            })
            out_path = os.path.join(restore_dir, f"{label}.txt")
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(f"=== Restored Block {i} ===\n")
                fh.write(hyp_raw.strip())
                fh.write("\n")
        except Exception as exc:
            print(f"✗  LỖI: {exc}")
            results.append({
                "idx": i,
                "reference": ref,
                "hypothesis": "",
                "raw_output": "",
                "exact": False,
                "cer_score": float("inf"),
                "elapsed": 0.0,
            })
            out_path = os.path.join(restore_dir, f"{label}.txt")
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(f"=== Restored Block {i} ===\n")
                fh.write("\n")

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
    print(f" ground_truth : {n}")
    print(f"  EMR      : {emr*100:.1f}%  ({exact_count}/{n} exact match)")
    print(f"  Avg CER  : {avg_cer:.2f}")
    print(f"  Tổng time: {total_time:.1f}s  (avg {total_time/n:.1f}s/msg)")
    print(f"  {model_name[:18]:<18} {emr*100:.0f}%  ← {avg_cer:.2f}")
    print("=" * 70)


def save_results(results: list[dict], model_name: str) -> None:
    os.makedirs("./output", exist_ok=True)
    out_path = f"./output/s2_result_{model_name.replace('/', '_').replace(':', '_')}.txt"
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
    mode = parser.add_mutually_exclusive_group(required=False)
    mode.add_argument(
        "--chunks-dir",
        help="Thư mục chứa AMC output .txt files (1 file/message). Ví dụ: output/amc",
    )
    mode.add_argument("--dumps-dir", help="Thư mục chứa .raw/.dmp files (1 dump/message)")

    parser.add_argument("--gt-file", help="File ground truth (1 dòng/message)")
    parser.add_argument("--pid", type=int, help="PID LINE Messenger (dùng với --dumps-dir)")
    parser.add_argument("--limit", type=int, help="Giới hạn số message (để test nhanh)")
    parser.add_argument("--throttle", type=float, default=0.5,
                        help="Delay giữa các API call (giây, mặc định 0.5)")
    args = parser.parse_args()

    # ---- Env defaults (để khỏi phải hard-code / nhập đi nhập lại) ----
    chunks_dir_env = os.environ.get("RAM_WEAVER_S2_CHUNKS_DIR")
    dumps_dir_env = os.environ.get("RAM_WEAVER_S2_DUMPS_DIR")

    if args.chunks_dir is not None:
        chunks_dir = args.chunks_dir
        dumps_dir = None
    elif args.dumps_dir is not None:
        chunks_dir = None
        dumps_dir = args.dumps_dir
    elif chunks_dir_env:
        chunks_dir = chunks_dir_env
        dumps_dir = None
    elif dumps_dir_env:
        chunks_dir = None
        dumps_dir = dumps_dir_env
    else:
        chunks_dir = None
        dumps_dir = None
    gt_file = args.gt_file or os.environ.get("RAM_WEAVER_GT_FILE")
    pid = args.pid
    if pid is None:
        pid_env = os.environ.get("RAM_WEAVER_PID")
        if pid_env and pid_env.isdigit():
            pid = int(pid_env)

    limit = args.limit
    if limit is None:
        limit_env = os.environ.get("RAM_WEAVER_LIMIT")
        if limit_env and limit_env.isdigit():
            limit = int(limit_env)

    throttle = args.throttle
    throttle_env = os.environ.get("RAM_WEAVER_THROTTLE")
    if throttle_env:
        try:
            throttle = float(throttle_env)
        except ValueError:
            pass

    print("=" * 70)
    print("RAM-Weaver — S2: Single Message Restoration Accuracy")
    print("=" * 70)

    labels: list[str] = []

    if not chunks_dir and not dumps_dir:
        print(
            "[ERROR] Cần chọn ít nhất 1 mode: --chunks-dir hoặc --dumps-dir\n"
            "  * CLI:  python experiment_s2.py --chunks-dir output/amc --gt-file ground_truth.txt\n"
            "  * .env: RAM_WEAVER_S2_CHUNKS_DIR=output/amc hoặc RAM_WEAVER_S2_DUMPS_DIR=dumps"
        )
        sys.exit(1)

    if chunks_dir:
        if not gt_file:
            print("[ERROR] Cần --gt-file hoặc set RAM_WEAVER_GT_FILE khi dùng --chunks-dir")
            sys.exit(1)
        print(f"\n[Chế độ A] AMC output files từ {chunks_dir}")
        chunk_files = list_chunk_files(chunks_dir, limit=limit)
        chunks = [f.read_text(encoding="utf-8") for f in chunk_files]
        ground_truths = load_ground_truth(gt_file, limit=limit)
        labels = [f.stem for f in chunk_files]

    else:
        if not gt_file or not pid:
            print("[ERROR] Cần --gt-file/RAM_WEAVER_GT_FILE và --pid/RAM_WEAVER_PID khi dùng --dumps-dir")
            sys.exit(1)
        print(f"\n[Chế độ B] Chạy AMC trên từng dump trong {dumps_dir}")

        dump_files = sorted(Path(dumps_dir).glob("*.raw"))
        if not dump_files:
            dump_files = sorted(Path(dumps_dir).glob("*.dmp"))
        if limit:
            dump_files = dump_files[:limit]

        ground_truths = load_ground_truth(gt_file, limit=limit)

        print(f"  Chạy AMC trên {len(dump_files)} dump files (tự reuse cache nếu đã có)...")
        chunks = []
        for df in dump_files:
            print(f"    AMC: {df.name}...", end=" ", flush=True)
            chunk, reused, path_used = run_amc_on_dump(str(df), pid)
            if chunk:
                tag = "CACHED" if reused else "OK"
                print(f"{tag} ({len(chunk)/1024:.1f} KB)")
                chunks.append(chunk)
            else:
                note = f" — {path_used}" if path_used else ""
                print(f"FAIL (chunk rỗng){note}")
                chunks.append("")
        labels = [df.stem for df in dump_files]

    if not chunks:
        print("[ERROR] Không có dữ liệu để chạy.")
        sys.exit(1)

    print("\nKhởi tạo LLM client...")
    llm_caller = make_llm_caller()

    provider = os.environ.get("RAM_WEAVER_LLM_PROVIDER", "gemini")
    model = os.environ.get("RAM_WEAVER_LLM_MODEL") or os.environ.get("RAM_WEAVER_GEMINI_MODEL", "gemini-2.5-flash")
    model_name = f"{provider}/{model}"

    print(f"\nChạy restore {len(chunks)} ground truth...\n")
    results = run_experiment(
        chunks,
        ground_truths,
        llm_caller,
        throttle=throttle,
        labels=labels,
    )

    print_results(results, model_name)
    save_results(results, model_name)


if __name__ == "__main__":
    main()