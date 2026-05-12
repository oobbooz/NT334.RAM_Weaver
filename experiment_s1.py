#!/usr/bin/env python
"""experiment_s1.py — Tái hiện S1: AMC Efficacy (Table 1 trong paper).

So sánh 3 phương pháp xử lý memory dump:
  Baseline 1 — Process memory + naïve strings (toàn bộ raw strings)
  Baseline 2 — AMC's AME (extraction) + strings (không có JSON filter)
  RAM-Weaver  — Full AMC pipeline (AME + regex filter + JSON-like filter)

Với mỗi phương pháp đo:
  - Input data size (KB) gửi cho LLM
  - Signal-to-Noise Ratio (SNR, dB)
  - LLM Feasibility (có vượt context limit không)
  - Initial CER (trước LLM) — so với ground truth message
  - Final CER  (sau LLM)   — sau khi LLM phục hồi

Paper Table 1 kết quả mong đợi:
  Baseline 1: ~34 MB, -47.33 dB, Infeasible, CER=57416
  Baseline 2: ~5.58 MB, -39.41 dB, Infeasible, CER=9537
  RAM-Weaver: ~15.8 KB, -10.53 dB, Feasible, Initial CER=18.61, Final CER=0.20
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import re
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

# Import metrics từ llm/metrics.py để nhất quán
from llm.metrics import snr_db, character_error_rate as cer, initial_cer

# LLM context limit giả định (GPT-o3 / Gemini Flash ≈ 128k tokens ≈ 500k chars)
LLM_MAX_CHARS = 500_000




# ── Strings extraction helper ─────────────────────────────────────────────────

def extract_strings_from_bytes(data: bytes) -> list[str]:
    """Trích xuất ASCII/UTF-8 và UTF-16LE strings từ binary data."""
    strings: list[str] = []
    for m in re.finditer(rb"[\x20-\x7e\x09\x0a\x0d]{8,}", data):
        try:
            strings.append(m.group().decode("utf-8", errors="ignore"))
        except Exception:
            pass
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){8,}", data):
        try:
            decoded = m.group().decode("utf-16-le", errors="ignore")
            if len(decoded) >= 8:
                strings.append(decoded)
        except Exception:
            pass
    return strings


# ── Baseline 1: naïve strings từ toàn bộ process memory ─────────────────────

def baseline1_naive_strings(dump_path: str) -> tuple[str, str]:
    """Trích xuất tất cả printable string runs từ file dump.

    FIX 1 (SNR): Trả về (pre_filter_text, output_text).
    pre_filter_text = toàn bộ strings extracted (mẫu số SNR).
    output_text = cũng là toàn bộ strings (Baseline 1 không filter).

    Returns:
        (pre_filter_text, output_text)
        — pre_filter_text dùng làm mẫu số trong snr_db()
        — output_text là text thực sự gửi cho LLM (hoặc đo CER)
    """
    print("  [Baseline 1] Đọc toàn bộ raw dump và trích strings...")
    raw_size = os.path.getsize(dump_path)

    with open(dump_path, "rb") as fh:
        data = fh.read()

    strings = extract_strings_from_bytes(data)
    combined = "\n".join(strings)

    print(f"    Raw dump size: {raw_size / 1024 / 1024:.2f} MB")
    print(f"    Strings extracted: {len(strings):,}  text size: {len(combined)/1024:.1f} KB")
    # Baseline 1: không có bước filter, nên pre_filter == output
    return combined, combined


# ── Baseline 2: AMC extraction + strings (không JSON filter) ─────────────────

def baseline2_ame_strings(dump_path: str, pid: int) -> tuple[str, str]:
    """Chạy AME (Volatility extraction) nhưng chỉ dùng strings thô, bỏ qua JSON filter. """
    print("  [Baseline 2] AME extraction + strings (không JSON filter)...")
    try:
        from config import AMCConfig
        from amc.extractor import AdaptiveMemoryExtractor
    except ImportError as e:
        print(f"    Import lỗi: {e}. Fallback sang raw dump strings.")
        return baseline1_naive_strings(dump_path)

    cfg = AMCConfig()
    extractor = AdaptiveMemoryExtractor(cfg)
    binary_files = extractor.extract(dump_path, pid)

    if not binary_files:
        print("    Không extract được VAD regions. Fallback sang raw dump.")
        return baseline1_naive_strings(dump_path)

    strings: list[str] = []
    total_raw_bytes = 0
    for fpath in binary_files:
        total_raw_bytes += os.path.getsize(fpath)
        with open(fpath, "rb") as fh:
            data = fh.read()
        strings.extend(extract_strings_from_bytes(data))

    combined = "\n".join(strings)
    print(f"    VAD regions: {len(binary_files)}  raw binary: {total_raw_bytes/1024/1024:.2f} MB")
    print(f"    Strings extracted: {len(strings):,}  text size: {len(combined)/1024:.1f} KB")
    # Baseline 2: không filter, pre_filter == output
    return combined, combined


# ── RAM-Weaver: Full AMC pipeline ─────────────────────────────────────────────

def ramweaver_full_amc(dump_path: str, pid: int) -> tuple[str, str]:
    """Chạy full AMC pipeline (AME + regex filter + JSON-like filter). """
    print("  [RAM-Weaver] Full AMC pipeline...")
    try:
        from config import AMCConfig
        from amc.pipeline import AdaptiveMemoryCarver
        from amc.extractor import AdaptiveMemoryExtractor
    except ImportError as e:
        print(f"    Import lỗi: {e}")
        return "", ""

    cfg = AMCConfig()

    # Lấy pre_filter_text (strings từ VAD — giống Baseline 2)
    extractor = AdaptiveMemoryExtractor(cfg)
    binary_files = extractor.extract(dump_path, pid)

    pre_filter_strings: list[str] = []
    if binary_files:
        for fpath in binary_files:
            with open(fpath, "rb") as fh:
                data = fh.read()
            pre_filter_strings.extend(extract_strings_from_bytes(data))
    else:
        # Fallback: dùng raw dump
        with open(dump_path, "rb") as fh:
            data = fh.read()
        pre_filter_strings = extract_strings_from_bytes(data)

    pre_filter_text = "\n".join(pre_filter_strings)
    print(f"    Pre-filter text size (mẫu số SNR): {len(pre_filter_text)/1024:.1f} KB")

    # Chạy full pipeline để lấy output_text
    amc = AdaptiveMemoryCarver(cfg)
    output_path = amc.run(dump_path, pid, output_name="s1_amc_output.txt")

    if not output_path or not os.path.isfile(output_path):
        print("    AMC pipeline thất bại.")
        return pre_filter_text, ""

    output_text = Path(output_path).read_text(encoding="utf-8")
    print(f"    AMC output size (tử số SNR): {len(output_text)/1024:.2f} KB")
    print(f"    Chunks: {output_text.count('---CHUNK---') + 1}")
    return pre_filter_text, output_text


# ── LLM restore (chỉ gọi khi feasible) ───────────────────────────────────────

def llm_restore(text: str, ground_truth: str) -> tuple[str, float]:
    """Gửi text cho LLM để restore, trả về (restored_text, final_cer)."""
    try:
        from config import LLMConfig
        from llm.client import create_client
        from llm.prompts import RESTORE_SYSTEM_PROMPT, RESTORE_BATCH_USER_TEMPLATE
    except ImportError as e:
        print(f"    Không import được LLM modules: {e}")
        return "", float("inf")

    cfg = LLMConfig()
    llm = create_client(cfg)

    user_msg = RESTORE_BATCH_USER_TEMPLATE.format(content=text[:cfg.max_input_chars])
    print(f"    Gọi LLM ({cfg.provider}/{cfg.model})...")
    t0 = time.time()
    try:
        result = llm.generate(RESTORE_SYSTEM_PROMPT, user_msg).strip()
        elapsed = time.time() - t0
        final_cer_val = cer(ground_truth, result)
        print(f"    LLM done ({elapsed:.1f}s), Final CER={final_cer_val:.4f}")
        return result, final_cer_val
    except Exception as exc:
        print(f"    LLM lỗi: {exc}")
        return "", float("inf")


# ── In bảng kết quả ───────────────────────────────────────────────────────────

def print_table(rows: list[dict]) -> None:
    """In bảng giống Table 1 trong paper."""
    print()
    print("=" * 95)
    print(f"{'Method':<32} {'Size':>10} {'SNR (dB)':>10} {'Feasible':>10} {'Init CER':>12} {'Final CER':>10}")
    print("-" * 95)
    for r in rows:
        feasible = " Yes" if r["feasible"] else " No"
        init_cer_str = f"{r['init_cer']:.2f}" if r["init_cer"] < 1e9 else "N/A"
        final_cer_str = f"{r['final_cer']:.4f}" if r["final_cer"] < 1e9 else "N/A"
        size_kb = r["size_kb"]
        size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.2f} MB"
        print(f"{r['method']:<32} {size_str:>10} {r['snr']:>10.2f} {feasible:>10} {init_cer_str:>12} {final_cer_str:>10}")
    print()

    # So sánh với paper
    print("Paper Table 1 (expected):")
    print("-" * 95)
    paper_rows = [
        ("Baseline 1 (naïve strings)",  "~34.10 MB", "-47.33", "No",  "57416.48", "N/A"),
        ("Baseline 2 (AME + strings)",  "~5.58 MB",  "-39.41", "No",  "9537.20",  "N/A"),
        ("RAM-Weaver (full AMC)",        "~15.80 KB", "-10.53", "Yes", "18.61",    "0.20"),
    ]
    for name, size, snr, feas, iniCER, finCER in paper_rows:
        print(f"{name:<32} {size:>10} {snr:>10} {feas:>10} {iniCER:>12} {finCER:>10}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="S1: AMC Efficacy experiment")
    parser.add_argument("dump_path", help="Đường dẫn file memory dump (.vmem/.raw/.dmp)")
    parser.add_argument("pid", type=int, help="PID của process LINE Messenger")
    parser.add_argument("ground_truth", nargs="?", default="",
                        help="Ground truth message text (để tính CER)")
    parser.add_argument("--gt-file", help="File chứa ground truth (1 message/dòng, dùng dòng đầu)")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Bỏ qua bước LLM restore (chỉ đo preprocessing)")
    args = parser.parse_args()

    gt = args.ground_truth
    if args.gt_file and os.path.isfile(args.gt_file):
        lines = Path(args.gt_file).read_text(encoding="utf-8").strip().splitlines()
        gt = lines[0] if lines else gt

    if not gt:
        print("  Không có ground truth → Init CER và Final CER sẽ không tính được.")
        print("  Thêm ground truth message vào argument hoặc --gt-file để đo đầy đủ.")

    if not os.path.isfile(args.dump_path):
        print(f"[ERROR] Không tìm thấy dump file: {args.dump_path}")
        sys.exit(1)

    print("=" * 95)
    print("RAM-Weaver — S1: AMC Efficacy Experiment")
    print(f"Dump: {args.dump_path}  |  PID: {args.pid}")
    print("=" * 95)

    rows = []

    # ── Baseline 1 ──────────────────────────────────────────────────────────
    print("\n[1/3] Baseline 1: Process Memory + Naïve strings")
    b1_pre, b1_out = baseline1_naive_strings(args.dump_path)
    # SNR calculation: use raw file size as denominator (total bytes)
    # Paper style: SNR B1 = -47.33 dB (signal=extracted_text, noise=raw_dump)
    b1_raw_size = os.path.getsize(args.dump_path)
    b1_snr_paper_style = snr_db(len(b1_out), b1_raw_size) if b1_raw_size > 0 and len(b1_out) > 0 else float("-inf")
    b1_feasible = len(b1_out) <= LLM_MAX_CHARS
    b1_init_cer = initial_cer(b1_out, gt) if gt else float("inf")
    rows.append({
        "method": "Baseline 1 (naïve strings)",
        "size_kb": len(b1_out) / 1024,
        "snr": b1_snr_paper_style,
        "feasible": b1_feasible,
        "init_cer": b1_init_cer,
        "final_cer": float("inf"),
    })
    print(f"  Text size: {len(b1_out)/1024/1024:.2f} MB  SNR: {b1_snr_paper_style:.2f} dB  Feasible: {b1_feasible}")
    if gt:
        print(f"  Init CER: {b1_init_cer:.2f}")

    # ── Baseline 2 ──────────────────────────────────────────────────────────
    print("\n[2/3] Baseline 2: AME + strings (không JSON filter)")
    b2_pre, b2_out = baseline2_ame_strings(args.dump_path, args.pid)
    # SNR B2: signal = strings text (len(b2_out)), noise = pre-filter strings (len(b2_pre))
    # Use pre-filter text as proxy for total bytes extracted
    b2_snr = snr_db(len(b2_out), len(b2_pre)) if len(b2_pre) > 0 else float("-inf")
    b2_feasible = len(b2_out) <= LLM_MAX_CHARS
    b2_init_cer = initial_cer(b2_out, gt) if gt else float("inf")
    rows.append({
        "method": "Baseline 2 (AME + strings)",
        "size_kb": len(b2_out) / 1024,
        "snr": b2_snr,
        "feasible": b2_feasible,
        "init_cer": b2_init_cer,
        "final_cer": float("inf"),
    })
    print(f"  Text size: {len(b2_out)/1024/1024:.2f} MB  SNR: {b2_snr:.2f} dB  Feasible: {b2_feasible}")
    if gt:
        print(f"  Init CER: {b2_init_cer:.2f}")

    # ── RAM-Weaver ──────────────────────────────────────────────────────────
    print("\n[3/3] RAM-Weaver: Full AMC pipeline")
    rw_pre, rw_out = ramweaver_full_amc(args.dump_path, args.pid)
    # SNR RAM-Weaver: signal=filtered_output (len(rw_out)), noise=pre-filter (len(rw_pre))
    rw_snr = snr_db(len(rw_out), len(rw_pre)) if rw_pre else float("-inf")
    rw_feasible = len(rw_out) <= LLM_MAX_CHARS
    rw_init_cer = initial_cer(rw_out, gt) if gt else float("inf")

    rw_final_cer = float("inf")
    if rw_feasible and not args.skip_llm and gt and rw_out:
        print("  Feasible! Gọi LLM để restore...")
        _, rw_final_cer = llm_restore(rw_out, gt)
    elif args.skip_llm:
        print("  Bỏ qua LLM (--skip-llm)")
    elif not rw_out:
        print("  AMC output rỗng, bỏ qua LLM.")

    rows.append({
        "method": "RAM-Weaver (full AMC)",
        "size_kb": len(rw_out) / 1024,
        "snr": rw_snr,
        "feasible": rw_feasible,
        "init_cer": rw_init_cer,
        "final_cer": rw_final_cer,
    })
    print(f"  Output size: {len(rw_out)/1024:.2f} KB  SNR: {rw_snr:.2f} dB  Feasible: {rw_feasible}")
    if gt:
        print(f"  Init CER: {rw_init_cer:.2f}  Final CER: {rw_final_cer:.4f}")

    # ── Bảng kết quả ────────────────────────────────────────────────────────
    print_table(rows)

    # Lưu kết quả
    os.makedirs("./output", exist_ok=True)
    out_path = "./output/s1_result.txt"
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("S1 AMC Efficacy Result\n")
        fh.write(f"Dump: {args.dump_path}  PID: {args.pid}\n\n")
        for r in rows:
            fin = f"{r['final_cer']:.4f}" if r['final_cer'] < 1e9 else "N/A"
            ini = f"{r['init_cer']:.2f}" if r['init_cer'] < 1e9 else "N/A"
            fh.write(
                f"{r['method']}: size={r['size_kb']:.1f}KB snr={r['snr']:.2f}dB "
                f"feasible={r['feasible']} init_cer={ini} final_cer={fin}\n"
            )
    print(f"Kết quả đã lưu: {out_path}")


def _get_vad_binary_size(dump_path: str, pid: int) -> int:
    """Lấy tổng kích thước binary của VAD regions (dùng để tính SNR đúng).

    Nếu không import được AMC modules, trả về 0 (caller sẽ dùng fallback).
    """
    try:
        from config import AMCConfig
        from amc.extractor import AdaptiveMemoryExtractor
        cfg = AMCConfig()
        extractor = AdaptiveMemoryExtractor(cfg)
        binary_files = extractor.extract(dump_path, pid)
        if binary_files:
            return sum(os.path.getsize(f) for f in binary_files)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    main()