import logging
import os
import time

try:
    from google import genai  # type: ignore[reportMissingImports]
    from google.genai import types  # type: ignore[reportMissingImports]
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("[WARNING] google-genai chưa được cài. Chạy: pip install google-genai")

from .config import LLMConfig

log = logging.getLogger("LLM")


class GeminiClient:
    def __init__(self, config: LLMConfig | None = None):
        self.cfg = config or LLMConfig()
        if not GEMINI_AVAILABLE:
            raise ImportError("Cài google-genai trước: pip install google-genai")
        api_key = self.cfg.api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "Cần GEMINI_API_KEY. Set bằng:\n"
                "  export GEMINI_API_KEY='your-key'\n"
                "  hoặc truyền vào LLMConfig(api_key='...')"
            )
        self.client = genai.Client(api_key=api_key)
        log.info(f"Gemini client khởi tạo thành công (model: {self.cfg.model})")

    def generate(self, system_prompt: str, user_message: str) -> str:
        for attempt in range(self.cfg.max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.cfg.model,
                    contents=user_message,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        max_output_tokens=self.cfg.max_output_tokens,
                        temperature=self.cfg.temperature,
                    )
                )
                return response.text or ""
            except Exception as e:
                log.warning(f"Gemini API lỗi (lần {attempt+1}/{self.cfg.max_retries}): {e}")
                if attempt < self.cfg.max_retries - 1:
                    time.sleep(self.cfg.retry_delay * (attempt + 1))
                else:
                    raise
        return ""
