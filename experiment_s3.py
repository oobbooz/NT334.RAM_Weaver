#!/usr/bin/env python
"""experiment_s3.py — Tái hiện S3: Truy vấn điều tra theo ngữ cảnh (Hình 2 trong bài báo).

Mục đích:
    Đánh giá khả năng phân tích ngữ cảnh của hệ thống trên cuộc trò chuyện
    gồm nhiều lượt trao đổi qua LINE Messenger.

Quy trình:
    Giai đoạn 1  – AMC (Adaptive Memory Carver):
                Đọc file dump → trích strings → lọc regex + JSON-key filter
                → output: các chunk JSON có liên quan đến tin nhắn.
    Giai đoạn 2  – Truy vấn điều tra qua LLM:
                Nạp chunks vào ForensicQueryEngine → gửi từng câu hỏi →
                LLM phân tích ngữ cảnh, chuyển đổi timestamp, xác định
                người gửi, tổng hợp bản ghi dễ đọc.

Thiết lập (paper S3):
    - Tập dữ liệu: Topical-Chat, kịch bản LINE 2 tài khoản nhắn tin qua lại.
    - File dump: line_s3.dmp (dump process RAM của LINE Desktop trên Windows).
    - Đánh giá định tính: Correctness (tính chính xác) & Completeness (tính đầy đủ).

Sử dụng:
    # Chạy đầy đủ (Giai đoạn 1 + Giai đoạn 2 tương tác):
    python experiment_s3.py /path/to/line_s3.dmp --pid <PID>

    # Nếu đã có file output AMC (bỏ qua Giai đoạn 1):
    python experiment_s3.py /path/to/line_s3.dmp --amc-file output/amc/s3_amc_output.txt

    # Dùng danh sách câu hỏi từ JSON thay vì tương tác:
    python experiment_s3.py /path/to/line_s3.dmp --pid <PID> --queries s3_queries.json

    # Chạy với câu hỏi từ bài báo (Hình 2) mà không cần PID Volatility:
    python experiment_s3.py /path/to/line_s3.dmp --no-volatility --queries s3_queries.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
for p in [str(ROOT), str(ROOT / "llm"), str(ROOT / "amc")]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ── Load .env ─────────────────────────────────────────────────────────────────
from config import load_env
load_env(ROOT / ".env")

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("ram_weaver.s3")

# ── Banner helper ─────────────────────────────────────────────────────────────
_DIVIDER = "=" * 70


def _banner(title: str) -> None:
    print(f"\n{_DIVIDER}", flush=True)
    print(f"  {title}", flush=True)
    print(_DIVIDER, flush=True)


# =============================================================================
# Giai đoạn 1 – AMC: trích xuất chunk từ dump
# =============================================================================

def _extract_strings_raw(data: bytes, min_len: int = 8) -> list[str]:
    """Trích ASCII/UTF-8 và UTF-16-LE string runs từ binary data."""
    strings: list[str] = []
    # ASCII / UTF-8
    for m in re.finditer(rb"[\x20-\x7e\x09\x0a\x0d]{" + str(min_len).encode() + rb",}", data):
        try:
            strings.append(m.group().decode("utf-8", errors="ignore"))
        except Exception:
            pass
    # UTF-16-LE
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){" + str(min_len).encode() + rb",}", data):
        try:
            decoded = m.group().decode("utf-16-le", errors="ignore")
            if len(decoded) >= min_len:
                strings.append(decoded)
        except Exception:
            pass
    return strings


def run_amc_stage(dump_path: str, pid: int | None, no_volatility: bool = False) -> str:
    """Chạy Giai đoạn 1 (AMC) và trả về text output (string).

    Ưu tiên:
    1. Nếu có Volatility + PID → dùng AdaptiveMemoryCarver đầy đủ.
    2. Nếu --no-volatility hoặc không có PID → dùng direct raw strings +
       JSON-key filter (fallback không cần Volatility).

    Trả về:
        AMC output text (các chunks ghép bằng \\n---CHUNK---\\n).
    """
    _banner("GIAI ĐOẠN 1 — Adaptive Memory Carver (AMC)")
    print(f"  Dump : {dump_path}", flush=True)
    dump_size_mb = os.path.getsize(dump_path) / 1024 / 1024
    print(f"  Size : {dump_size_mb:.2f} MB", flush=True)

    if not no_volatility and pid is not None:
        print(f"  PID  : {pid}  →  Chạy đầy đủ quy trình AMC (Volatility)...", flush=True)
        try:
            from config import AMCConfig
            from amc.pipeline import AdaptiveMemoryCarver

            cfg = AMCConfig()
            amc = AdaptiveMemoryCarver(cfg)
            output_path = amc.run(dump_path, pid, output_name="s3_amc_output.txt")
            if output_path and os.path.isfile(output_path):
                text = Path(output_path).read_text(encoding="utf-8")
                size_kb = len(text) / 1024
                chunk_count = text.count("---CHUNK---") + 1
                print(f"  ✓ AMC output: {size_kb:.2f} KB  |  {chunk_count} chunks", flush=True)
                print(f"  ✓ Đã lưu: {output_path}", flush=True)
                return text
            else:
                print("  ⚠ Volatility pipeline không tạo output. Chuyển sang fallback.", flush=True)
        except Exception as exc:
            print(f"  ⚠ Lỗi Volatility pipeline: {exc}", flush=True)
            print("    Chuyển sang direct string extraction (fallback)...", flush=True)

    # ── Fallback: direct raw string extraction + JSON-key filter ─────────────
    print("  → Fallback: đọc thẳng file dump, extract strings + JSON filter...", flush=True)
    t0 = time.time()

    with open(dump_path, "rb") as fh:
        raw_data = fh.read()

    strings = _extract_strings_raw(raw_data, min_len=8)
    print(f"  Strings extracted: {len(strings):,}  ({sum(len(s) for s in strings)/1024:.1f} KB)", flush=True)

    # JSON-key filter (giống AMC filtering.py nhưng tự chứa)
    JSON_KEYS = {"text", "from", "to", "createdTime", "chatId",
                 "contentType", "status", "type", "id"}
    _KEYS_RE = re.compile(
        "|".join(re.escape(f'"{k}"') for k in JSON_KEYS)
    )
    NOISE_PATTERNS = [
        re.compile(r"[A-Za-z]:\\[\w\\/. -]+"),           # Windows paths
        re.compile(r"\{[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\}"),  # GUIDs
        re.compile(r"https?://[^\s\"'<>]{5,200}"),        # URLs
        re.compile(r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}"),    # IPv4
        re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),         # base64
        re.compile(r"\\x[0-9a-fA-F]{2}"),                # hex escapes
    ]

    threshold = 2
    _FLAT_JSON_RE = re.compile(r"\{[^{}]*\}")
    chunks: list[str] = []
    seen: set[int] = set()

    for s in strings:
        # Áp noise filter
        cleaned = s
        for pat in NOISE_PATTERNS:
            cleaned = pat.sub("", cleaned)
        cleaned = cleaned.strip()
        if len(cleaned) < 8:
            continue

        # Kiểm tra JSON-key coverage
        if len(_KEYS_RE.findall(cleaned)) >= threshold:
            h = hash(cleaned)
            if h not in seen:
                seen.add(h)
                chunks.append(cleaned)
            continue

        # Block parse
        for block_match in _FLAT_JSON_RE.finditer(cleaned):
            try:
                obj = json.loads(block_match.group())
                if not isinstance(obj, dict):
                    continue
                serialised = json.dumps(obj, ensure_ascii=False)
                if len(_KEYS_RE.findall(serialised)) >= threshold:
                    h = hash(serialised)
                    if h not in seen:
                        seen.add(h)
                        chunks.append(serialised)
                    break
            except json.JSONDecodeError:
                pass

    elapsed = time.time() - t0
    text = "\n---CHUNK---\n".join(chunks)
    size_kb = len(text) / 1024
    print(f"  ✓ Chunks sau filter: {len(chunks):,}  ({size_kb:.2f} KB)  [{elapsed:.1f}s]", flush=True)

    # Lưu output
    os.makedirs("./output_s3/amc", exist_ok=True)
    out_path = "./output_s3/amc/s3_amc_output.txt"
    Path(out_path).write_text(text, encoding="utf-8")
    print(f"  ✓ Đã lưu: {out_path}", flush=True)

    return text


# =============================================================================
# Giai đoạn 2 – Truy vấn điều tra qua LLM
# =============================================================================

def _init_llm_engine(memory_text: str):
    """Khởi tạo ForensicQueryEngine với memory data."""
    from config import LLMConfig
    from llm.client import create_client

    cfg = LLMConfig()
    llm = create_client(cfg)
    print(f"  LLM : {cfg.provider} / {cfg.model}", flush=True)
    print(f"  Data: {len(memory_text):,} chars đã sẵn sàng.", flush=True)

    # Import engine (flat path)
    sys.path.insert(0, str(ROOT / "llm"))
    from llm.query_engine import ForensicQueryEngine
    engine = ForensicQueryEngine(llm_client=llm, config=cfg)
    engine.load_memory_string(memory_text)
    return engine, cfg


def _run_predefined_queries(
    engine,
    queries: list[dict],
    throttle: float = 1.0,
) -> list[dict]:
    """Chạy từng câu hỏi được định nghĩa trước và lưu kết quả.

    Đánh giá định tính (S3): Correctness + Completeness.

    Tham số:
        engine: ForensicQueryEngine đã nạp memory data.
        queries: Danh sách dict {"id", "query", "ground_truth" (tùy chọn)}.
        throttle: Thời gian nghỉ giữa các lần gọi API (giây).

    Trả về:
        Danh sách kết quả gồm "id", "query", "answer", "ground_truth".
    """
    results: list[dict] = []
    total = len(queries)
    os.makedirs("./output_s3", exist_ok=True)

    for i, q in enumerate(queries, 1):
        qid = q.get("id", f"q{i:03d}")
        query_text = q.get("query", "")
        ground_truth = q.get("ground_truth", "")

        print(f"\n  [{i}/{total}] Query [{qid}]:", flush=True)
        print(f"  ❓ {query_text}", flush=True)
        print("  🔍 Đang phân tích...", flush=True)

        t0 = time.time()
        try:
            answer = engine.query(query_text)
            elapsed = time.time() - t0

            print(f"\n{'─'*60}", flush=True)
            print(f"  ✅ Trả lời ({elapsed:.1f}s):", flush=True)
            print(answer, flush=True)
            print(f"{'─'*60}", flush=True)

            if ground_truth:
                print(f"\n  📋 Ground Truth:", flush=True)
                print(f"  {ground_truth}", flush=True)

            results.append({
                "id": qid,
                "query": query_text,
                "answer": answer,
                "ground_truth": ground_truth,
                "elapsed_s": round(elapsed, 2),
            })

            # Lưu answer riêng
            ans_path = f"./output_s3/s3_answer_{qid}.txt"
            with open(ans_path, "w", encoding="utf-8") as fh:
                fh.write(f"Query [{qid}]: {query_text}\n\n")
                fh.write("=== LLM Answer ===\n")
                fh.write(answer)
                fh.write("\n")
                if ground_truth:
                    fh.write("\n=== Ground Truth ===\n")
                    fh.write(ground_truth)
                    fh.write("\n")
            print(f"  💾 Đã lưu: {ans_path}", flush=True)

        except Exception as exc:
            elapsed = time.time() - t0
            print(f"  ❌ Lỗi ({elapsed:.1f}s): {exc}", flush=True)
            results.append({
                "id": qid,
                "query": query_text,
                "answer": f"ERROR: {exc}",
                "ground_truth": ground_truth,
                "elapsed_s": round(elapsed, 2),
            })

        if i < total:
            print(f"  ⏳ Chờ {throttle:.0f}s trước query tiếp theo (tránh quota 429)...", flush=True)
            time.sleep(throttle)

    return results


def _print_s3_summary(results: list[dict], amc_size_kb: float) -> None:
    """In bảng tóm tắt kết quả S3 (định tính, không tính CER)."""
    _banner("KẾT QUẢ S3 — Contextual Forensic Querying")
    print(f"  AMC output size    : {amc_size_kb:.2f} KB  (feasible cho LLM)", flush=True)
    print(f"  Số câu hỏi thực thi: {len(results)}", flush=True)

    avg_time = (
        sum(r.get("elapsed_s", 0) for r in results) / len(results)
        if results else 0
    )
    print(f"  Thời gian TB/query : {avg_time:.1f}s", flush=True)

    print(f"\n  {'ID':<10} {'Query':<45} {'Time':>6}", flush=True)
    print(f"  {'─'*63}", flush=True)
    for r in results:
        q_short = r["query"][:42] + ("..." if len(r["query"]) > 42 else "")
        t_str = f"{r.get('elapsed_s', 0):.1f}s"
        status = "OK" if not r["answer"].startswith("ERROR") else "ERR"
        print(f"  [{status}] {r['id']:<8} {q_short:<45} {t_str:>6}", flush=True)

    print(f"\n  📄 Paper S3 evaluation (định tính):", flush=True)
    print(f"     Correctness  : LLM phân tích đúng cấu trúc JSON, chuyển", flush=True)
    print(f"                    đổi timestamp Unix→local, xác định người gửi.", flush=True)
    print(f"     Completeness : LLM liệt kê đầy đủ tất cả tin nhắn thỏa điều kiện.", flush=True)
    print(f"     → Đây là đánh giá định tính (manual review); xem output_s3/s3_answer_*.txt", flush=True)


def _save_s3_results(results: list[dict], amc_size_kb: float) -> None:
    """Lưu toàn bộ kết quả S3 ra JSON."""
    os.makedirs("./output_s3", exist_ok=True)
    out = {
        "experiment": "S3 — Contextual Forensic Querying",
        "amc_size_kb": round(amc_size_kb, 2),
        "results": results,
    }
    path = "./output_s3/s3_results.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(f"\n  💾 Kết quả tổng hợp: {path}", flush=True)


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
                description="S3: Truy vấn điều tra theo ngữ cảnh – mô phỏng Hình 2 trong bài báo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ví dụ:
    # Đầy đủ (Giai đoạn 1: Volatility + Giai đoạn 2: tương tác):
    python experiment_s3.py /path/to/line_s3.dmp --pid 4832

  # Bỏ qua Volatility, dùng raw dump trực tiếp:
    python experiment_s3.py /path/to/line_s3.dmp --no-volatility

  # Dùng AMC output đã có sẵn:
    python experiment_s3.py /path/to/line_s3.dmp --amc-file output/amc/s3_amc_output.txt

    # Chạy câu hỏi từ JSON (không tương tác):
    python experiment_s3.py /path/to/line_s3.dmp --no-volatility --queries s3_queries.json
        """
    )

    parser.add_argument(
        "dump_path",
        help='Đường dẫn file memory dump (.dmp/.raw/.vmem), ví dụ: /path/to/line_s3.dmp',
    )
    parser.add_argument(
        "--pid", type=int, default=None,
        help="PID của process LINE Messenger (cần nếu dùng Volatility extraction)",
    )
    parser.add_argument(
        "--no-volatility", action="store_true",
        help="Bỏ qua Volatility, đọc thẳng raw dump và chạy JSON-key filter",
    )
    parser.add_argument(
        "--amc-file", default=None,
        help="Dùng file AMC output đã có sẵn (bỏ qua Giai đoạn 1 hoàn toàn)",
    )
    parser.add_argument(
        "--queries", default=None,
        help="File JSON chứa danh sách câu hỏi [{\"id\",\"query\",\"ground_truth\"}]",
    )

    parser.add_argument(
        "--throttle", type=float, default=60.0,
        help="Delay giữa các API call (giây, mặc định 60.0 để tránh Gemini Free Tier quota 429)",
    )
    parser.add_argument(
        "--interactive", action="store_true",
        help="Sau khi chạy queries file, mở REPL tương tác để tiếp tục hỏi",
    )
    args = parser.parse_args()

    # ── Kiểm tra dump file ────────────────────────────────────────────────────
    if not os.path.isfile(args.dump_path):
        print(f"[ERROR] Không tìm thấy dump file: {args.dump_path}", flush=True)
        sys.exit(1)

    _banner("RAM-Weaver — S3: Truy vấn điều tra theo ngữ cảnh")
    print(f"  Dump : {args.dump_path}", flush=True)
    print(f"  Paper: Figure 2 – LLM-driven Forensic Querying", flush=True)

    # ── Giai đoạn 1 – AMC ─────────────────────────────────────────────────────
    if args.amc_file:
        # Dùng file đã có
        amc_path = args.amc_file
        if not os.path.isfile(amc_path):
            print(f"[ERROR] AMC file không tồn tại: {amc_path}", flush=True)
            sys.exit(1)
        memory_text = Path(amc_path).read_text(encoding="utf-8")
        amc_size_kb = len(memory_text) / 1024
        _banner("GIAI ĐOẠN 1 — AMC (bỏ qua, dùng file có sẵn)")
        print(f"  File : {amc_path}", flush=True)
        print(f"  Size : {amc_size_kb:.2f} KB  |  "
              f"{memory_text.count('---CHUNK---') + 1} chunks", flush=True)
    else:
        memory_text = run_amc_stage(
            args.dump_path,
            pid=args.pid,
            no_volatility=args.no_volatility,
        )
        amc_size_kb = len(memory_text) / 1024

    if not memory_text.strip():
        print("[ERROR] AMC output rỗng. Kiểm tra dump file hoặc thay đổi --pid.", flush=True)
        sys.exit(1)

    # ── Giai đoạn 2 – Bộ máy LLM ─────────────────────────────────────────────
    _banner("GIAI ĐOẠN 2 — Bộ máy truy vấn điều tra bằng LLM")
    try:
        engine, cfg = _init_llm_engine(memory_text)
    except Exception as exc:
        print(f"[ERROR] Khởi tạo LLM engine thất bại: {exc}", flush=True)
        print("  Kiểm tra GEMINI_API_KEY / OPENAI_API_KEY trong .env", flush=True)
        sys.exit(1)

    # ── Xây dựng danh sách câu hỏi ───────────────────────────────────────────
    queries: list[dict] = []

    # Câu hỏi từ file JSON (s3_queries.json)
    if args.queries and os.path.isfile(args.queries):
        with open(args.queries, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, list):
            queries.extend(loaded)
            print(f"  Đã tải {len(loaded)} câu hỏi từ {args.queries}", flush=True)
        else:
            print(f"  ⚠ File {args.queries} không phải danh sách JSON, bỏ qua.", flush=True)

    # [EDITED]: Đã gỡ bỏ các câu hỏi demo hardcode (paper_fig2, paper_speakers, paper_topic)
    # và tham số `--paper-demo` khỏi argparse để tập trung hoàn toàn vào việc load cấu hình 
    # câu hỏi ngoại vi từ file `s3_queries.json`. Điều này giúp code gọn gàng và dễ mở rộng.
    
    # ── Chạy predefined queries ───────────────────────────────────────────────
    results: list[dict] = []
    if queries:
        _banner("CHẠY CÂU HỎI ĐÃ ĐỊNH NGHĨA")
        results = _run_predefined_queries(engine, queries, throttle=args.throttle)
        _print_s3_summary(results, amc_size_kb)
        _save_s3_results(results, amc_size_kb)

    # ── Interactive session ───────────────────────────────────────────────────
    if args.interactive or not queries:
        # Nếu không có queries file thì mặc định vào interactive
        _banner("PHIÊN TRUY VẤN ĐIỀU TRA TƯƠNG TÁC")
        print(
            "  💡 Gõ câu hỏi bằng tiếng Anh hoặc tiếng Việt.\n"
            "  💡 Lệnh: exit | quit | history | save\n"
            "  💡 Ví dụ paper Figure 2:\n"
            '     "List all messages after Wed Jul 02 2025 14:15:22 (Taipei time)."\n',
            flush=True,
        )
        try:
            engine.interactive_session()
        except KeyboardInterrupt:
            print("\n  Session kết thúc.", flush=True)

    print(f"\n{_DIVIDER}", flush=True)
    print("  RAM-Weaver S3 hoàn tất.", flush=True)
    print(f"{_DIVIDER}\n", flush=True)


if __name__ == "__main__":
    main()