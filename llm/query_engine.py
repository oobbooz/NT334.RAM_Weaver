"""Truy vấn điều tra theo ngữ cảnh – Giai đoạn 2, Nhiệm vụ B (flat layout).

Phân tích memory data từ LINE Messenger và trả lời câu hỏi điều tra
bằng ngôn ngữ tự nhiên thông qua LLM.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import sys

from client import BaseLLMClient
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LLMConfig
from prompts import FORENSIC_QUERY_SYSTEM_PROMPT, FORENSIC_QUERY_USER_TEMPLATE

log = logging.getLogger("ram_weaver.llm.query_engine")

_HISTORY_OUTPUT_PATH = "./output_s3/query_history.json"

# ---------------------------------------------------------------------------
# Tiện ích timestamp
# ---------------------------------------------------------------------------

_VN_UTC_OFFSET = 7 * 3600  # UTC+7

_TIME_AFTER_RE = re.compile(
    r"(?:after|from|since|sau)\s+"
    r"(?:\w+\s+\w+\s+\d+\s+\d{4}\s+)?(\d{1,2}):(\d{2})(?::(\d{2}))?",
    re.IGNORECASE,
)
_TIME_BEFORE_RE = re.compile(
    r"(?:before|until|trước)\s+"
    r"(?:\w+\s+\w+\s+\d{1,2}\s+\d{4}\s+)?(\d{1,2}):(\d{2})(?::(\d{2}))?",
    re.IGNORECASE,
)


def _parse_hms(m: re.Match) -> int:
    h, mn, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    return h * 3600 + mn * 60 + s


def _chunk_tod_vn(chunk: str) -> int | None:
    """Trả về giây-trong-ngày (VN) của createdTime đầu tiên trong chunk."""
    for raw in re.findall(r'"createdTime"\s*:\s*(\d+)', chunk):
        ts = int(raw)
        if ts > 1_000_000_000_000:
            ts //= 1000
        if ts < 1_000_000_000:
            continue
        return (ts + _VN_UTC_OFFSET) % 86400
    return None


def _inject_vn_timestamps(memory_data: str) -> str:
    """Thêm field _vnTime (đã convert) vào mỗi createdTime trong data.

    Giúp LLM đọc thẳng giờ VN thay vì tự tính từ Unix ms.
    Ví dụ: "createdTime":1778652021082
        →  "createdTime":1778652021082,"_vnTime":"2026-05-13 13:00:21 ICT"
    """
    def _replace(m: re.Match) -> str:
        ct = int(m.group(1))
        if ct < 1_000_000_000_000:
            return m.group(0)  # không phải ms timestamp → giữ nguyên
        ts_s = ct // 1000
        try:
            vn_dt = datetime.datetime.utcfromtimestamp(ts_s) + datetime.timedelta(hours=7)
            tag = f',\"_vnTime\":\"{vn_dt.strftime("%Y-%m-%d %H:%M:%S")} ICT\"'
        except Exception:
            return m.group(0)
        return m.group(0) + tag

    return re.sub(r'"createdTime"\s*:\s*(\d+)', _replace, memory_data)


def _prefilter_chunks(memory_data: str, query: str) -> tuple[str, str]:
    """Lọc chunk theo điều kiện thời gian rút ra từ query.

    Trả về:
        (filtered_data, note)
    """
    after_m = _TIME_AFTER_RE.search(query)
    before_m = _TIME_BEFORE_RE.search(query)

    if not after_m and not before_m:
        return memory_data, ""

    after_sec = _parse_hms(after_m) if after_m else None
    before_sec = _parse_hms(before_m) if before_m else None

    chunks = memory_data.split("---CHUNK---")
    kept: list[str] = []
    skipped = 0

    for chunk in chunks:
        tod = _chunk_tod_vn(chunk)
        if tod is None:
            kept.append(chunk)  # không có timestamp → giữ (metadata)
            continue
        if after_sec is not None and tod <= after_sec:
            skipped += 1
            continue
        if before_sec is not None and tod >= before_sec:
            skipped += 1
            continue
        kept.append(chunk)

    note_parts = []
    if after_sec is not None:
        h, r = divmod(after_sec, 3600)
        note_parts.append(f"after {h:02d}:{r//60:02d}:{r%60:02d} ICT")
    if before_sec is not None:
        h, r = divmod(before_sec, 3600)
        note_parts.append(f"before {h:02d}:{r//60:02d}:{r%60:02d} ICT")

    note = (
        f"[Pre-filter: kept {len(kept)}/{len(chunks)} chunks "
        f"({', '.join(note_parts)}); skipped {skipped}]"
    )
    log.info(note)
    print(f"  🔎 {note}", flush=True)
    return "---CHUNK---".join(kept), note

_HISTORY_OUTPUT_PATH = "./output_s3/query_history.json"


def _print_answer(answer: str) -> None:
    """In kết quả ra stdout, flush ngay lập tức để không bị buffer."""
    divider = "=" * 60
    print(f"\n{divider}", flush=True)
    print(answer, flush=True)
    print(f"{divider}\n", flush=True)


def _save_answer(query: str, answer: str, index: int) -> None:
    """Lưu từng answer ra file riêng trong output_s3/ ngay sau khi nhận được."""
    os.makedirs("./output_s3", exist_ok=True)
    path = f"./output_s3/answer_{index:03d}.txt"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"Query: {query}\n\n")
        fh.write(answer)
    log.info("Answer saved: %s", path)


class ForensicQueryEngine:
    """LLM-powered forensic query engine."""

    def __init__(
        self,
        llm_client: BaseLLMClient,
        config: LLMConfig | None = None,
    ) -> None:
        self.llm = llm_client
        self.cfg = config or LLMConfig()
        self._memory_data: str = ""

    # ------------------------------------------------------------------ #
    # Nạp dữ liệu                                                          #
    # ------------------------------------------------------------------ #

    def load_memory_file(self, chunks_file: str) -> None:
        """Load AMC chunk output từ file."""
        with open(chunks_file, "r", encoding="utf-8") as fh:
            self._memory_data = fh.read()
        log.info(
            "Loaded %d chars from '%s'.", len(self._memory_data), chunks_file
        )

    def load_memory_string(self, memory_data: str) -> None:
        """Nạp output AMC trực tiếp từ chuỗi."""
        self._memory_data = memory_data
        log.info("Loaded %d chars of memory data.", len(memory_data))

    # ------------------------------------------------------------------ #
    # Truy vấn                                                             #
    # ------------------------------------------------------------------ #

    def query(self, investigator_query: str) -> str:
        """Gửi câu hỏi điều tra và trả về kết quả phân tích từ LLM.

        Quy trình:
          1. Pre-filter chunks theo điều kiện thời gian (nếu có) → giảm input size
          2. Inject _vnTime đã convert sẵn vào mỗi createdTime → LLM không tự tính
          3. Truncate nếu vẫn vượt max_input_chars
          4. Gửi LLM

        Ngoại lệ:
            RuntimeError: Nếu chưa load memory data.
        """
        if not self._memory_data:
            raise RuntimeError(
                "Chua load memory data. Goi load_memory_file() truoc."
            )

        # Bước 1: Pre-filter theo thời gian (Python lọc chính xác)
        data, _note = _prefilter_chunks(self._memory_data, investigator_query)

        # Bước 2: Inject _vnTime đã convert → LLM đọc thẳng, không hallucinate
        data = _inject_vn_timestamps(data)

        # Bước 3: Truncate nếu cần
        if len(data) > self.cfg.max_input_chars:
            log.warning(
                "Memory data (%d chars) > limit (%d). Truncating.",
                len(data), self.cfg.max_input_chars,
            )
            data = data[: self.cfg.max_input_chars]

        user_msg = FORENSIC_QUERY_USER_TEMPLATE.format(
            memory_data=data,
            query=investigator_query,
        )
        log.info("Forensic query: '%s'", investigator_query)
        result = self.llm.generate(FORENSIC_QUERY_SYSTEM_PROMPT, user_msg) or ""
        return result.strip()

    # ------------------------------------------------------------------ #
    # Phiên tương tác                                                      #
    # ------------------------------------------------------------------ #

    def interactive_session(self) -> None:
        """REPL tương tác – mỗi câu hỏi/đáp án được lưu và flush ngay.

        - Kết quả flush stdout ngay (không bị buffer terminal cắt).
        - Mỗi answer được lưu riêng vào output_s3/answer_NNN.txt.
        - Toàn bộ lịch sử lưu vào output_s3/query_history.json khi thoát.
        - Gõ 'save' để xem đường dẫn file vừa lưu.
        - Gõ 'history' để xem lại tất cả câu hỏi đã hỏi trong session.
        """
        if not self._memory_data:
            print("Loi: Chua load memory data!", flush=True)
            return

        print("\n" + "=" * 60, flush=True)
        print("RAM-Weaver – Interactive Forensic Query Session", flush=True)
        print(f"Memory: {len(self._memory_data):,} chars da san sang.", flush=True)
        print("Lenh: exit | quit | history | save", flush=True)
        print("=" * 60 + "\n", flush=True)

        history: list[dict[str, str]] = []
        last_save_path: str = ""

        while True:
            try:
                # flush=True để prompt hiện ngay trong mọi terminal
                print("Query: ", end="", flush=True)
                raw = sys.stdin.readline()
                if raw == "":          # EOF (Ctrl+D)
                    print("\nSession ket thuc.", flush=True)
                    break
                raw = raw.strip()
            except KeyboardInterrupt:
                print("\nSession ket thuc.", flush=True)
                break

            # Lệnh đặc biệt
            if raw.lower() in {"exit", "quit", "q"}:
                print("Session ket thuc.", flush=True)
                break

            if raw.lower() == "history":
                if not history:
                    print("(Chua co query nao trong session nay.)\n", flush=True)
                else:
                    for i, item in enumerate(history, 1):
                        print(f"  [{i:02d}] {item['query']}", flush=True)
                    print("", flush=True)
                continue

            if raw.lower() == "save":
                if last_save_path:
                    print(f"Answer cuoi da luu tai: {last_save_path}\n", flush=True)
                else:
                    print("(Chua co answer nao duoc luu.)\n", flush=True)
                continue

            if not raw:
                continue

            # Gửi query
            print("\nDang phan tich...\n", flush=True)
            try:
                answer = self.query(raw)

                # In toàn bộ answer, flush ngay
                _print_answer(answer)

                # Lưu answer ra file ngay lập tức
                idx = len(history) + 1
                _save_answer(raw, answer, idx)
                last_save_path = f"./output_s3/answer_{idx:03d}.txt"

                history.append({"query": raw, "answer": answer})

            except Exception as exc:  # noqa: BLE001
                print(f"[LOI] {exc}\n", flush=True)

        # Lưu toàn bộ lịch sử session
        if history:
            os.makedirs(os.path.dirname(_HISTORY_OUTPUT_PATH) or ".", exist_ok=True)
            with open(_HISTORY_OUTPUT_PATH, "w", encoding="utf-8") as fh:
                json.dump(history, fh, ensure_ascii=False, indent=2)
            print(f"Lich su session da luu: {_HISTORY_OUTPUT_PATH}", flush=True)