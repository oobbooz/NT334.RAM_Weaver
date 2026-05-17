#!/usr/bin/env python
"""experiment_s1.py — Tái hiện S1: AMC Efficacy (Table 1 trong paper).

So sánh 3 phương pháp xử lý memory dump:
  Baseline 1 — Process memory + naïve strings (toàn bộ raw strings)
  Baseline 2 — AMC's AME (extraction) + strings (không có JSON filter)
  RAM-Weaver  — Full AMC pipeline (AME + regex filter + JSON-like filter)
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

# Load .env (project root) – không override biến môi trường đã set
from config import load_env
load_env(ROOT / ".env")

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

# Import nguyên bản từ metrics.py của bạn
from llm.metrics import snr_db, character_error_rate as cer, initial_cer

# LLM context limit giả định (GPT-o3 / Gemini Flash ≈ 128k tokens ≈ 500k chars)
LLM_MAX_CHARS = 500_000


# ── Strings extraction helpers ────────────────────────────────────────────────

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

def extract_strings_from_file_chunked(file_path: str, chunk_size: int = 10 * 1024 * 1024) -> list[str]:
    """Đọc file theo chunk 10MB để tránh tràn RAM WSL."""
    strings = []
    overlap = 100 # Giữ lại 100 byte cuối nối sang chunk sau để tránh cắt đôi chuỗi
    
    with open(file_path, "rb") as fh:
        while True:
            data = fh.read(chunk_size)
            if not data:
                break
            
            strings.extend(extract_strings_from_bytes(data))
            
            if len(data) == chunk_size:
                fh.seek(-overlap, os.SEEK_CUR)
                
    return strings


# ── Baseline 1: naïve strings từ toàn bộ process memory ─────────────────────

def baseline1_naive_strings(dump_path: str) -> tuple[str, str]:
    print("  [Baseline 1] Đọc chunked raw dump và trích strings...")
    raw_size = os.path.getsize(dump_path)

    # Đã thay bằng chunked read để tránh crash WSL
    strings = extract_strings_from_file_chunked(dump_path)
    combined = "\n".join(strings)

    print(f"    Raw dump size: {raw_size / 1024 / 1024:.2f} MB")
    print(f"    Strings extracted: {len(strings):,}  text size: {len(combined)/1024:.1f} KB")
    return combined, combined


# ── Baseline 2: trích xuất AME + strings (không JSON filter) ────────────────

def baseline2_ame_strings(dump_path: str, pid: int) -> tuple[str, str]:
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
        # Thay bằng chunked read
        strings.extend(extract_strings_from_file_chunked(fpath))

    combined = "\n".join(strings)
    print(f"    VAD regions: {len(binary_files)}  raw binary: {total_raw_bytes/1024/1024:.2f} MB")
    print(f"    Strings extracted: {len(strings):,}  text size: {len(combined)/1024:.1f} KB")
    return combined, combined


# ── RAM-Weaver: chạy đầy đủ quy trình AMC ───────────────────────────────────

def ramweaver_full_amc(dump_path: str, pid: int) -> tuple[str, str]:
    print("  [RAM-Weaver] Full AMC pipeline...")
    try:
        from config import AMCConfig
        from amc.pipeline import AdaptiveMemoryCarver
        from amc.extractor import AdaptiveMemoryExtractor
    except ImportError as e:
        print(f"    Import lỗi: {e}")
        return "", ""

    cfg = AMCConfig()

    extractor = AdaptiveMemoryExtractor(cfg)
    binary_files = extractor.extract(dump_path, pid)

    pre_filter_strings: list[str] = []
    if binary_files:
        for fpath in binary_files:
            # Thay bằng chunked read
            pre_filter_strings.extend(extract_strings_from_file_chunked(fpath))
    else:
        # Thay bằng chunked read
        pre_filter_strings = extract_strings_from_file_chunked(dump_path)

    pre_filter_text = "\n".join(pre_filter_strings)
    print(f"    Pre-filter text size (mẫu số SNR): {len(pre_filter_text)/1024:.1f} KB")

    amc = AdaptiveMemoryCarver(cfg)
    output_path = amc.run(dump_path, pid, output_name="s1_amc_output.txt")

    if not output_path or not os.path.isfile(output_path):
        print("    AMC pipeline thất bại.")
        return pre_filter_text, ""

    output_text = Path(output_path).read_text(encoding="utf-8")
    print(f"    AMC output size (tử số chuẩn SNR): {len(output_text)/1024:.2f} KB")
    print(f"    Chunks: {output_text.count('---CHUNK---') + 1}")
    return pre_filter_text, output_text

def parse_single_msg_output(llm_output: str) -> str:
    """Lấy phần message_text từ output format [HH:MM:SS] / sender: / text"""
    lines = llm_output.strip().splitlines()
    # Bỏ dòng timestamp [HH:MM:SS] và dòng sender_id:
    for i, line in enumerate(lines):
        if re.match(r"^\[?\d{2}:\d{2}:\d{2}\]?$", line.strip()):
            continue
        if re.match(r"^.+:$", line.strip()):
            # Dòng tiếp theo trở đi là message text
            return "\n".join(lines[i+1:]).strip()
    return llm_output.strip()  # fallback nếu không match format

# ── LLM restore (chỉ gọi khi feasible) ───────────────────────────────────────

def llm_restore(text: str, ground_truth: str) -> tuple[str, float]:
    try:
        from config import LLMConfig
        from llm.client import create_client
        from llm.prompts import RESTORE_SYSTEM_PROMPT, RESTORE_BATCH_USER_TEMPLATE, RESTORE_S2_SINGLE_MSG_PROMPT
    except ImportError as e:
        print(f"    Không import được LLM modules: {e}")
        return "", float("inf")

    cfg = LLMConfig()
    llm = create_client(cfg)

    user_msg = RESTORE_BATCH_USER_TEMPLATE.format(content=text[:cfg.max_input_chars])
    print(f"    Gọi LLM ({cfg.provider}/{cfg.model})...")
    t0 = time.time()
    try:
        raw = llm.generate(RESTORE_S2_SINGLE_MSG_PROMPT, user_msg).strip()
        result = parse_single_msg_output(raw)
        elapsed = time.time() - t0
        final_cer_val = cer(ground_truth, result)
        print(f"    LLM done ({elapsed:.1f}s), Final CER={final_cer_val:.4f}")
        return result, final_cer_val
    except Exception as exc:
        print(f"    LLM lỗi: {exc}")
        return "", float("inf")


# ── In bảng kết quả ───────────────────────────────────────────────────────────

def print_table(rows: list[dict]) -> None:
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


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="S1: AMC Efficacy experiment")
    parser.add_argument("dump_path", help="Đường dẫn file memory dump (.vmem/.raw/.dmp)")
    parser.add_argument("pid", type=int, help="PID của process LINE Messenger")
    parser.add_argument("ground_truth", nargs="?", default="",
                        help="Ground truth message text (để tính CER)")
    parser.add_argument("--gt-file", help="File chứa ground truth")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Bỏ qua bước LLM restore")
    args = parser.parse_args()

    gt = args.ground_truth
    if args.gt_file and os.path.isfile(args.gt_file):
        lines = Path(args.gt_file).read_text(encoding="utf-8").strip().splitlines()
        gt = lines[0] if lines else gt

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
    b1_feasible = len(b1_out) <= LLM_MAX_CHARS
    b1_init_cer = initial_cer(b1_out, gt) if gt else float("inf")

    # ── Baseline 2 ──────────────────────────────────────────────────────────
    print("\n[2/3] Baseline 2: AME + strings (không JSON filter)")
    b2_pre, b2_out = baseline2_ame_strings(args.dump_path, args.pid)
    b2_feasible = len(b2_out) <= LLM_MAX_CHARS
    b2_init_cer = initial_cer(b2_out, gt) if gt else float("inf")

    # ── RAM-Weaver ──────────────────────────────────────────────────────────
    print("\n[3/3] RAM-Weaver: Full AMC pipeline")
    rw_pre, rw_out = ramweaver_full_amc(args.dump_path, args.pid)
    rw_feasible = len(rw_out) <= LLM_MAX_CHARS
    rw_init_cer = initial_cer(rw_out, gt) if gt else float("inf")

    rw_final_cer = float("inf")
    if rw_feasible and not args.skip_llm and gt and rw_out:
        print("  Feasible! Gọi LLM để restore...")
        _, rw_final_cer = llm_restore(rw_out, gt)

    # ── TÍNH TOÁN LẠI SNR CHUẨN ──────────────────────────────────────────────
    # Sử dụng kích thước output của AMC làm "signal" tiêu chuẩn (hoặc ground truth)
    signal_size = len(gt)

    b1_snr = snr_db(signal_size, len(b1_out)) if len(b1_out) > 0 else float("-inf")
    b2_snr = snr_db(signal_size, len(b2_out)) if len(b2_out) > 0 else float("-inf")
    rw_snr = snr_db(signal_size, len(rw_out)) if len(rw_out) > 0 else float("-inf")

    # Đưa kết quả vào bảng
    rows.append({
        "method": "Baseline 1 (naïve strings)",
        "size_kb": len(b1_out) / 1024,
        "snr": b1_snr,
        "feasible": b1_feasible,
        "init_cer": b1_init_cer,
        "final_cer": float("inf"),
    })
    
    rows.append({
        "method": "Baseline 2 (AME + strings)",
        "size_kb": len(b2_out) / 1024,
        "snr": b2_snr,
        "feasible": b2_feasible,
        "init_cer": b2_init_cer,
        "final_cer": float("inf"),
    })

    rows.append({
        "method": "RAM-Weaver (full AMC)",
        "size_kb": len(rw_out) / 1024,
        "snr": rw_snr,
        "feasible": rw_feasible,
        "init_cer": rw_init_cer,
        "final_cer": rw_final_cer,
    })

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

if __name__ == "__main__":
    main()