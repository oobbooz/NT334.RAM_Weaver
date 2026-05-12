"""Contextual Forensic Querying – Stage 2, Task B (flat layout).

Phân tích memory data từ LINE Messenger và trả lời câu hỏi điều tra
bằng ngôn ngữ tự nhiên thông qua LLM.
"""

from __future__ import annotations

import json
import logging
import os
import sys

from client import BaseLLMClient
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LLMConfig
from prompts import FORENSIC_QUERY_SYSTEM_PROMPT, FORENSIC_QUERY_USER_TEMPLATE

log = logging.getLogger("ram_weaver.llm.query_engine")

_HISTORY_OUTPUT_PATH = "./output/query_history.json"


def _print_answer(answer: str) -> None:
    """In kết quả ra stdout, flush ngay lập tức để không bị buffer."""
    divider = "=" * 60
    print(f"\n{divider}", flush=True)
    print(answer, flush=True)
    print(f"{divider}\n", flush=True)


def _save_answer(query: str, answer: str, index: int) -> None:
    """Lưu từng answer ra file riêng trong output/ ngay sau khi nhận được."""
    os.makedirs("./output", exist_ok=True)
    path = f"./output/answer_{index:03d}.txt"
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
    # Data loading                                                         #
    # ------------------------------------------------------------------ #

    def load_memory_file(self, chunks_file: str) -> None:
        """Load AMC chunk output từ file."""
        with open(chunks_file, "r", encoding="utf-8") as fh:
            self._memory_data = fh.read()
        log.info(
            "Loaded %d chars from '%s'.", len(self._memory_data), chunks_file
        )

    def load_memory_string(self, memory_data: str) -> None:
        """Load AMC output trực tiếp từ string."""
        self._memory_data = memory_data
        log.info("Loaded %d chars of memory data.", len(memory_data))

    # ------------------------------------------------------------------ #
    # Query                                                                #
    # ------------------------------------------------------------------ #

    def query(self, investigator_query: str) -> str:
        """Gửi câu hỏi điều tra và trả về kết quả phân tích từ LLM.

        Raises:
            RuntimeError: Nếu chưa load memory data.
        """
        if not self._memory_data:
            raise RuntimeError(
                "Chua load memory data. Goi load_memory_file() truoc."
            )

        data = self._memory_data
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
    # Interactive session                                                  #
    # ------------------------------------------------------------------ #

    def interactive_session(self) -> None:
        """REPL tương tác – mỗi câu hỏi/đáp án được lưu và flush ngay.

        - Kết quả flush stdout ngay (không bị buffer terminal cắt).
        - Mỗi answer được lưu riêng vào output/answer_NNN.txt.
        - Toàn bộ lịch sử lưu vào output/query_history.json khi thoát.
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
                last_save_path = f"./output/answer_{idx:03d}.txt"

                history.append({"query": raw, "answer": answer})

            except Exception as exc:  # noqa: BLE001
                print(f"[LOI] {exc}\n", flush=True)

        # Lưu toàn bộ lịch sử session
        if history:
            os.makedirs(os.path.dirname(_HISTORY_OUTPUT_PATH) or ".", exist_ok=True)
            with open(_HISTORY_OUTPUT_PATH, "w", encoding="utf-8") as fh:
                json.dump(history, fh, ensure_ascii=False, indent=2)
            print(f"Lich su session da luu: {_HISTORY_OUTPUT_PATH}", flush=True)
