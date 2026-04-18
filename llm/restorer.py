import logging
import time

from .client import GeminiClient
from .config import LLMConfig
from .prompts import RESTORE_SYSTEM_PROMPT, RESTORE_USER_TEMPLATE

log = logging.getLogger("LLM")


class TextRestorer:
    def __init__(self, llm_client: GeminiClient, config: LLMConfig | None = None):
        self.llm = llm_client
        self.cfg = config or LLMConfig()

    def restore(self, memory_fragment: str) -> str:
        if len(memory_fragment) > self.cfg.max_input_chars:
            log.warning(f"Fragment quá dài ({len(memory_fragment)} chars), truncate về {self.cfg.max_input_chars}")
            memory_fragment = memory_fragment[:self.cfg.max_input_chars]
        user_msg = RESTORE_USER_TEMPLATE.format(fragment=memory_fragment)
        log.debug(f"Gửi fragment {len(memory_fragment)} chars đến Gemini để restore...")
        result = self.llm.generate(RESTORE_SYSTEM_PROMPT, user_msg) or ""
        return result.strip()

    def restore_batch(self, chunks: list[str]) -> list[str]:
        results = []
        for i, chunk in enumerate(chunks):
            log.info(f"Đang restore chunk {i+1}/{len(chunks)}...")
            results.append(self.restore(chunk))
            time.sleep(0.5)
        return results

    def restore_from_file(self, chunks_file: str) -> list[str]:
        with open(chunks_file, "r", encoding="utf-8") as f:
            content = f.read()
        log.info(f"Đang gộp toàn bộ dữ liệu ({len(content)} chars) vào 1 request duy nhất...")
        user_msg = f"""Dưới đây là TOÀN BỘ các đoạn memory fragment trích xuất được từ LINE Messenger.
        Hãy đọc tất cả, loại bỏ nhiễu, sắp xếp theo thời gian (createdTime) và phục hồi lại danh sách tin nhắn gốc:
        
        --- MEMORY DATA START ---
        {content}
        --- MEMORY DATA END ---
        """
        result = self.llm.generate(RESTORE_SYSTEM_PROMPT, user_msg) or ""
        return [result.strip()]
