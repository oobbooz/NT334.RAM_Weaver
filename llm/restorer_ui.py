"""High-Fidelity Text Restoration – UI Version (Task A).
Sử dụng bộ luật V2 (Phân loại 🟢 Đáng tin / 🟡 Tham khảo) dành riêng cho giao diện.
"""

from __future__ import annotations

import logging
import time
import sys
import os

from client import BaseLLMClient

# Thêm thư mục gốc vào path để import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LLMConfig

# Nạp thẳng Prompt V2 từ prompts_for_ui
from prompts_for_ui import (
    RESTORE_SYSTEM_PROMPT_V2,
    RESTORE_BATCH_USER_TEMPLATE_V2,
)

log = logging.getLogger("ram_weaver.llm.restorer_ui")
_CHUNK_SLEEP_SECONDS = 0.5

# Template dự phòng cho chế độ xử lý từng chunk
RESTORE_USER_TEMPLATE_V2 = """\
Below is a noisy memory chunk from LINE Messenger process memory.
Reconstruct the original user message text and categorize it (🟢 RELIABLE or 🟡 REFERENCE):

--- MEMORY FRAGMENT START ---
{fragment}
--- MEMORY FRAGMENT END ---

Restored Evidence Report:\
"""

class TextRestorerUI:
    """Phiên bản UI: Restores clean message text with Confidence Tagging (🟢/🟡)."""

    def __init__(
        self,
        llm_client: BaseLLMClient,
        config: LLMConfig | None = None,
    ) -> None:
        self.llm = llm_client
        self.cfg = config or LLMConfig()

    def restore_from_file(self, chunks_file: str) -> list[str]:
        with open(chunks_file, "r", encoding="utf-8") as fh:
            content = fh.read()

        if len(content) > self.cfg.max_input_chars:
            content = content[: self.cfg.max_input_chars]

        # Gọi trực tiếp các prompt V2
        user_msg = RESTORE_BATCH_USER_TEMPLATE_V2.format(content=content)
        result = self.llm.generate(RESTORE_SYSTEM_PROMPT_V2, user_msg) or ""
        return [result.strip()]

    def restore(self, memory_fragment: str) -> str:
        if len(memory_fragment) > self.cfg.max_input_chars:
            memory_fragment = memory_fragment[: self.cfg.max_input_chars]

        user_msg = RESTORE_USER_TEMPLATE_V2.format(fragment=memory_fragment)
        return (self.llm.generate(RESTORE_SYSTEM_PROMPT_V2, user_msg) or "").strip()

    def restore_batch(self, chunks: list[str]) -> list[str]:
        results: list[str] = []
        total = len(chunks)
        for i, chunk in enumerate(chunks, start=1):
            results.append(self.restore(chunk))
            if i < total:
                time.sleep(_CHUNK_SLEEP_SECONDS)
        return results
