"""Khôi phục văn bản (độ trung thực cao) – Giai đoạn 2, Nhiệm vụ A.

Bài báo đánh giá 2 chiến lược khôi phục:
- **Khôi phục từng chunk**: gửi từng chunk riêng lẻ (dễ cô lập lỗi).
- **Khôi phục theo batch**: gộp toàn bộ chunk vào 1 lần gọi LLM để khử trùng lặp
    và sắp xếp theo thời gian.

Trong case study LINE Messenger, tác giả dùng cách batch, nên
:meth:`restore_from_file` là luồng chính.
"""

from __future__ import annotations

import logging
import time

from client import BaseLLMClient
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LLMConfig
from prompts import (
    RESTORE_BATCH_USER_TEMPLATE,
    RESTORE_SYSTEM_PROMPT,
    RESTORE_USER_TEMPLATE,
)

log = logging.getLogger("ram_weaver.llm.restorer")

_CHUNK_SLEEP_SECONDS = 0.5  # delay giữa các lần gọi API (khi khôi phục từng chunk)


class TextRestorer:
    """Khôi phục text tin nhắn sạch từ mảnh dữ liệu nhiễu trong RAM thông qua LLM.

    Tham số:
        llm_client: Client LLM độc lập theo provider.
        config: Cấu hình LLM (giới hạn token/ký tự).
    """

    def __init__(
        self,
        llm_client: BaseLLMClient,
        config: LLMConfig | None = None,
    ) -> None:
        self.llm = llm_client
        self.cfg = config or LLMConfig()

    # ------------------------------------------------------------------ #
    # Luồng chính – khôi phục theo lô (khuyến nghị)                       #
    # ------------------------------------------------------------------ #

    def restore_from_file(self, chunks_file: str) -> list[str]:
        """Đọc file chunk và khôi phục toàn bộ fragment bằng 1 lần gọi LLM.

        Đây là cách dùng trong bài báo: gửi toàn bộ output AMC vào LLM để
        LLM có thể khử trùng lặp giữa các chunk và sắp xếp theo ``createdTime``.

        Tham số:
            chunks_file: Đường dẫn file output AMC (các chunk được ngăn bằng
                         ``---CHUNK---``).

        Trả về:
            Danh sách chứa 1 khối văn bản đã khôi phục.
        """
        with open(chunks_file, "r", encoding="utf-8") as fh:
            content = fh.read()

        log.info(
            "Batch restore: %d chars from '%s'.", len(content), chunks_file
        )

        if len(content) > self.cfg.max_input_chars:
            log.warning(
                "Content (%d chars) exceeds max_input_chars (%d). Truncating.",
                len(content), self.cfg.max_input_chars,
            )
            content = content[: self.cfg.max_input_chars]

        user_msg = RESTORE_BATCH_USER_TEMPLATE.format(content=content)
        result = self.llm.generate(RESTORE_SYSTEM_PROMPT, user_msg) or ""
        return [result.strip()]

    # ------------------------------------------------------------------ #
    # Luồng thay thế – khôi phục từng chunk                               #
    # ------------------------------------------------------------------ #

    def restore(self, memory_fragment: str) -> str:
        """Khôi phục một mảnh dữ liệu bộ nhớ.

        Tham số:
            memory_fragment: Chunk text thô lấy từ output AMC.

        Trả về:
            Nội dung tin nhắn đã làm sạch và ghép lại.
        """
        if len(memory_fragment) > self.cfg.max_input_chars:
            log.warning(
                "Fragment (%d chars) exceeds max_input_chars. Truncating.",
                len(memory_fragment),
            )
            memory_fragment = memory_fragment[: self.cfg.max_input_chars]

        user_msg = RESTORE_USER_TEMPLATE.format(fragment=memory_fragment)
        log.debug("Restoring fragment (%d chars)…", len(memory_fragment))
        return (self.llm.generate(RESTORE_SYSTEM_PROMPT, user_msg) or "").strip()

    def restore_batch(self, chunks: list[str]) -> list[str]:
        """Khôi phục từng chunk độc lập và trả về danh sách kết quả tương ứng.

        Hữu ích cho đánh giá (S2 trong bài báo) khi cần tính thước đo theo từng
        tin nhắn (EMR, CER).

        Tham số:
            chunks: Danh sách chunk thô.

        Trả về:
            Danh sách chuỗi đã khôi phục, mỗi phần tử ứng với 1 chunk đầu vào.
        """
        results: list[str] = []
        total = len(chunks)
        for i, chunk in enumerate(chunks, start=1):
            log.info("Restoring chunk %d / %d …", i, total)
            results.append(self.restore(chunk))
            if i < total:
                time.sleep(_CHUNK_SLEEP_SECONDS)
        return results
