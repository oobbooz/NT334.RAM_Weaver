"""Quy trình giai đoạn 2: Tái hiện dựa trên LLM.

Điều phối:
    - Nhiệm vụ A: Khôi phục văn bản (High-Fidelity Restoration)
    - Nhiệm vụ B: Truy vấn điều tra theo ngữ cảnh (Contextual Forensic Querying)

Cách dùng::

    from ram_weaver.llm import LLMConfig, LLMReconstructor

    rec = LLMReconstructor(LLMConfig(provider="openai"))
    results = rec.run_restoration("./output/amc_output.txt")
    answer  = rec.run_forensic_query(
        "./output/amc_output.txt",
        "Liệt kê các tin nhắn sau 14:15 (giờ địa phương).",
    )
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from client import BaseLLMClient, create_client
from query_engine import ForensicQueryEngine
from restorer import TextRestorer
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LLMConfig
log = logging.getLogger("ram_weaver.llm.pipeline")

_DEFAULT_RESTORED_OUTPUT = "./output/restored.txt"


class LLMReconstructor:
    """Bộ điều phối end-to-end cho giai đoạn 2.

    Tham số:
        config: Cấu hình LLM. Mặc định ``LLMConfig()`` (đọc provider/key từ env).
        llm_client: Client dựng sẵn (hữu ích cho test/tiêm phụ thuộc). Nếu truyền vào,
            sẽ dùng client này để gọi LLM; vẫn dùng `config` cho giới hạn input/output.
    """

    def __init__(
        self,
        config: Optional[LLMConfig] = None,
        llm_client: Optional[BaseLLMClient] = None,
    ) -> None:
        self.cfg = config or LLMConfig()
        self.llm: BaseLLMClient = llm_client or create_client(self.cfg)
        self.restorer = TextRestorer(self.llm, self.cfg)
        self.query_engine = ForensicQueryEngine(self.llm, self.cfg)

    # ------------------------------------------------------------------ #
    # Task A – Khôi phục văn bản                                          #
    # ------------------------------------------------------------------ #

    def run_restoration(
        self,
        chunks_file: str,
        output_file: str = _DEFAULT_RESTORED_OUTPUT,
    ) -> list[str]:
        """Khôi phục nội dung tin nhắn từ output AMC và ghi ra file.

        Tham số:
            chunks_file: Đường dẫn file chunk (output giai đoạn 1).
            output_file: Đường dẫn file ghi kết quả.

        Trả về:
            Danh sách các đoạn văn bản đã khôi phục.
        """
        log.info("=" * 60)
        log.info("Stage 2 – Task A: High-Fidelity Text Restoration")
        log.info("=" * 60)

        results = self.restorer.restore_from_file(chunks_file)

        os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as fh:
            for i, text in enumerate(results, start=1):
                fh.write(f"=== Restored Block {i} ===\n{text}\n\n")

        log.info("Restoration complete.  Output: %s", output_file)
        return results

    # ------------------------------------------------------------------ #
    # Task B – Truy vấn điều tra theo ngữ cảnh                            #
    # ------------------------------------------------------------------ #

    def run_forensic_query(self, chunks_file: str, query: str) -> str:
        """Trả lời một câu hỏi điều tra dựa trên file chunk.

        Tham số:
            chunks_file: Đường dẫn file chunk.
            query: Câu hỏi điều tra (ngôn ngữ tự nhiên).

        Trả về:
            Chuỗi phân tích do LLM sinh ra.
        """
        log.info("=" * 60)
        log.info("Stage 2 – Task B: Forensic Query")
        log.info("Query: %s", query)
        log.info("=" * 60)

        self.query_engine.load_memory_file(chunks_file)
        return self.query_engine.query(query)

    # ------------------------------------------------------------------ #
    # Phiên tương tác                                                     #
    # ------------------------------------------------------------------ #

    def run_interactive(self, chunks_file: str) -> None:
        """Chạy REPL tương tác để truy vấn điều tra.

        Tham số:
            chunks_file: Đường dẫn file chunk.
        """
        self.query_engine.load_memory_file(chunks_file)
        self.query_engine.interactive_session()
