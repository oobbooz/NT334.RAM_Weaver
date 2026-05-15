#!/usr/bin/env python
"""diagnose.py – So sánh dump thô và output chunk của AMC để xác định mất dữ liệu ở bước nào.

Cách dùng:
    python diagnose.py <dump_file_or_dmp_dir> <chunks_file>

Ví dụ:
    python diagnose.py source/mem_capture.raw output/amc_output.txt
    python diagnose.py vad_dumps/pid_8616/    output/amc_output.txt

Kết quả:
    - diagnose_report.txt  : báo cáo đầy đủ
    - missing_strings.txt  : các string có trong raw nhưng KHÔNG có trong chunks
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


# ── Các message ID cần kiểm tra (từ kết quả LLM) ────────────────────────────
KNOWN_MISSING_IDS = [
    "608393528720490821",
    "608393553735582226",
]

# ── String quan trọng cần tìm trong raw ─────────────────────────────────────
SEARCH_STRINGS = [
    # Message IDs bị mất
    "608393528720490821",
    "608393553735582226",
    # Text bị fragment
    "a hot ko",
    "anh k c",
    # Để verify các message đã tìm thấy
    "be bong bay",
    "nhok hat tieu",
    "banh xeo",
    "phuong my chi",
    "e ma, thay nguoi ta",
    "nguoi yeu cu",
    "em gai mua",
    "huong tram"
]


def read_binary_as_text(path: str) -> list[tuple[str, bytes]]:
    """Đọc file binary, trả về [(encoding, decoded_bytes), ...]."""
    results = []
    with open(path, "rb") as f:
        data = f.read()

    # UTF-8 / ASCII runs
    for m in re.finditer(rb"[\x20-\x7e\x09\x0a\x0d]{8,}", data):
        try:
            results.append(("utf8", m.group()))
        except Exception:
            pass

    # UTF-16-LE runs
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){8,}", data):
        try:
            decoded = m.group().decode("utf-16-le", errors="ignore")
            if len(decoded) >= 8:
                results.append(("utf16le", decoded.encode("utf-8")))
        except Exception:
            pass

    return results


def collect_raw_strings(source: str) -> set[str]:
    """Thu thập tất cả printable strings từ raw dump hoặc thư mục VAD dumps."""
    all_strings: set[str] = set()
    source_path = Path(source)

    files: list[Path] = []
    if source_path.is_dir():
        files = list(source_path.rglob("*.dmp"))
        print(f"[INFO] Tìm thấy {len(files)} file .dmp trong {source}")
    elif source_path.is_file():
        files = [source_path]
        print(f"[INFO] Đọc trực tiếp từ file: {source}")
    else:
        print(f"[ERROR] Không tìm thấy: {source}")
        sys.exit(1)

    total_bytes = 0
    for f in files:
        size = f.stat().st_size
        total_bytes += size
        pairs = read_binary_as_text(str(f))
        for _, b in pairs:
            try:
                all_strings.add(b.decode("utf-8", errors="ignore"))
            except Exception:
                pass

    print(f"[INFO] Tổng raw size: {total_bytes / 1024:.1f} KB")
    print(f"[INFO] Tổng strings extracted từ raw: {len(all_strings):,}")
    return all_strings


def collect_chunk_content(chunks_file: str) -> str:
    """Đọc toàn bộ nội dung chunk file."""
    with open(chunks_file, "r", encoding="utf-8") as f:
        return f.read()


def search_in_raw(needle: str, raw_strings: set[str]) -> list[str]:
    """Tìm needle trong tất cả raw strings, trả về danh sách strings chứa needle."""
    needle_lower = needle.lower()
    found = []
    for s in raw_strings:
        if needle_lower in s.lower():
            # Trả về đoạn xung quanh để có context
            idx = s.lower().find(needle_lower)
            start = max(0, idx - 60)
            end = min(len(s), idx + len(needle) + 60)
            found.append(s[start:end])
    return found


def main() -> None:
    if len(sys.argv) < 3:
        print("Cách dùng: python diagnose.py <raw_dump_or_vad_dir> <chunks_file>")
        print("Ví dụ: python diagnose.py source/mem_capture.raw output/amc_output.txt")
        sys.exit(1)

    raw_source = sys.argv[1]
    chunks_file = sys.argv[2]

    if not Path(chunks_file).is_file():
        print(f"[ERROR] Chunks file không tồn tại: {chunks_file}")
        sys.exit(1)

    print("=" * 60)
    print("RAM-Weaver Diagnostic Tool")
    print("Xác định mất dữ liệu ở bước nào: Raw→VAD hay VAD→Chunk")
    print("=" * 60)

    # Đọc raw
    print("\n[1/3] Đọc raw dump...")
    raw_strings = collect_raw_strings(raw_source)

    # Đọc chunks
    print("\n[2/3] Đọc AMC chunk output...")
    chunk_content = collect_chunk_content(chunks_file)
    print(f"[INFO] Chunk file size: {len(chunk_content) / 1024:.1f} KB")
    print(f"[INFO] Số chunks: {chunk_content.count('---CHUNK---') + 1}")

    # Phân tích
    print("\n[3/3] Phân tích từng string quan trọng...")
    print("=" * 60)

    report_lines = []
    report_lines.append("RAM-Weaver Diagnostic Report")
    report_lines.append("=" * 60)
    report_lines.append(f"Raw source  : {raw_source}")
    report_lines.append(f"Chunks file : {chunks_file}")
    report_lines.append("")

    missing_from_chunks = []  # có trong raw, không có trong chunk
    missing_from_raw = []     # không có trong raw (mất từ Volatility dump)

    for needle in SEARCH_STRINGS:
        in_raw = search_in_raw(needle, raw_strings)
        in_chunk = needle.lower() in chunk_content.lower()

        status_raw = f"✓ CÓ trong raw ({len(in_raw)} lần)" if in_raw else "✗ KHÔNG có trong raw"
        status_chunk = "✓ CÓ trong chunk" if in_chunk else "✗ KHÔNG có trong chunk"

        # Phán đoán
        if in_raw and in_chunk:
            verdict = "→ OK: Chuỗi được giữ nguyên qua cả 2 bước"
        elif in_raw and not in_chunk:
            verdict = "→ MẤT Ở BƯỚC 2 (AMC filtering loại bỏ)"
            missing_from_chunks.append(needle)
        elif not in_raw and in_chunk:
            verdict = "→ LẠ: không có trong raw nhưng có trong chunk (impossible)"
        else:
            verdict = "→ MẤT Ở BƯỚC 1 (Volatility không dump được, hoặc overwritten)"
            missing_from_raw.append(needle)

        line = (
            f"\nSearch: '{needle}'\n"
            f"  Raw   : {status_raw}\n"
            f"  Chunk : {status_chunk}\n"
            f"  {verdict}"
        )
        print(line)
        report_lines.append(line)

        # In context nếu có trong raw
        if in_raw:
            report_lines.append("  Context từ raw:")
            for ctx in in_raw[:2]:  # tối đa 2 context
                report_lines.append(f"    ...{repr(ctx.strip()[:120])}...")

    # Tóm tắt
    summary = f"""
{'=' * 60}
TỔNG KẾT CHẨN ĐOÁN
{'=' * 60}

Strings có trong raw NHƯNG bị lọc mất bởi AMC (bước 2):
{chr(10).join('  - ' + s for s in missing_from_chunks) or '  (Không có)'}

Strings KHÔNG tìm thấy trong raw dump (mất từ bước 1 / Volatility):
{chr(10).join('  - ' + s for s in missing_from_raw) or '  (Không có)'}

KẾT LUẬN:
"""
    if missing_from_raw:
        summary += (
            "  Dữ liệu bị mất ngay từ bước dump (Volatility không capture được).\n"
            "  Nguyên nhân có thể:\n"
            "    a) Memory region đó đã bị overwrite trước khi dump\n"
            "    b) PID sai → dump nhầm process\n"
            "    c) VAD region bị loại trừ bởi extraction mode\n"
            "  → Thử lại với extraction_mode=heap hoặc dump sớm hơn.\n"
        )
    if missing_from_chunks:
        summary += (
            "  Dữ liệu BỊ MẤT DO AMC FILTERING (bước 2).\n"
            "  Nguyên nhân có thể:\n"
            "    a) json_key_threshold quá cao (hiện tại = 2)\n"
            "    b) String ngắn hơn min_string_len (hiện tại = 8 chars)\n"
            "    c) Regex noise filter vô tình loại nhầm\n"
            "  → Thử giảm json_key_threshold=1 hoặc min_string_len=4.\n"
        )
    if not missing_from_raw and not missing_from_chunks:
        summary += "  Tất cả string quan trọng đều được giữ lại. Pipeline hoạt động đúng.\n"

    print(summary)
    report_lines.append(summary)

    # Lưu report
    os.makedirs("./output", exist_ok=True)
    report_path = "./output/diagnose_report.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))
    print(f"\n[OK] Báo cáo đầy đủ: {report_path}")


if __name__ == "__main__":
    main()
