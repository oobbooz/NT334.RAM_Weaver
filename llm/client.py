"""Unified LLM client – Google Gemini và OpenAI (flat layout).

Fix truncation: log finish_reason để chẩn đoán khi response bị cắt.
Gemini 2.5-flash hỗ trợ tới 65536 output tokens – tăng default lên 8192.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

from config import LLMConfig

log = logging.getLogger("ram_weaver.llm.client")


# --------------------------------------------------------------------------- #
# Abstract base                                                                #
# --------------------------------------------------------------------------- #

class BaseLLMClient(ABC):
    def __init__(self, config: LLMConfig) -> None:
        self.cfg = config

    @abstractmethod
    def generate(self, system_prompt: str, user_message: str) -> str:
        """Gửi prompt và trả về response text đầy đủ."""


# --------------------------------------------------------------------------- #
# Google Gemini                                                                #
# --------------------------------------------------------------------------- #

class GeminiClient(BaseLLMClient):
    """Wrapper quanh google-genai SDK với logging finish_reason."""

    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        try:
            from google import genai          # type: ignore[import]
            from google.genai import types    # type: ignore[import]
            self._genai = genai
            self._types = types
        except ImportError as exc:
            raise ImportError(
                "google-genai chua duoc cai. Chay: pip install google-genai"
            ) from exc

        api_key = config.api_key
        if not api_key:
            raise ValueError(
                "Can GEMINI_API_KEY. Set trong .env hoac export GEMINI_API_KEY=..."
            )
        self._client = self._genai.Client(api_key=api_key)
        log.info("GeminiClient ready (model: %s, max_tokens: %d).",
                 config.model, config.max_output_tokens)

    def generate(self, system_prompt: str, user_message: str) -> str:
        for attempt in range(1, self.cfg.max_retries + 1):
            try:
                response = self._client.models.generate_content(
                    model=self.cfg.model,
                    contents=user_message,
                    config=self._types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        max_output_tokens=self.cfg.max_output_tokens,
                        temperature=self.cfg.temperature,
                    ),
                )

                # ── Chẩn đoán truncation ─────────────────────────────────
                try:
                    reason = response.candidates[0].finish_reason
                    reason_name = str(reason)
                    if "MAX_TOKENS" in reason_name or reason_name == "2":
                        log.warning(
                            "Response bi cat vi MAX_TOKENS (finish_reason=%s). "
                            "Tang RAM_WEAVER_MAX_OUTPUT_TOKENS trong .env.",
                            reason_name,
                        )
                    elif "STOP" not in reason_name and "1" not in reason_name:
                        log.warning(
                            "finish_reason khong binh thuong: %s", reason_name
                        )
                except Exception:
                    pass  # finish_reason khong quan trong bang noi dung

                text = response.text or ""
                if not text:
                    # Thử lấy từ parts nếu .text bị None
                    try:
                        parts = response.candidates[0].content.parts
                        text = "".join(
                            p.text for p in parts if hasattr(p, "text")
                        )
                    except Exception:
                        pass

                return text

            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "Gemini API loi (lan %d/%d): %s",
                    attempt, self.cfg.max_retries, exc,
                )
                if attempt < self.cfg.max_retries:
                    time.sleep(self.cfg.retry_delay * attempt)
                else:
                    raise
        return ""


# --------------------------------------------------------------------------- #
# OpenAI (GPT-o3 – 100% EMR trong paper)                                      #
# --------------------------------------------------------------------------- #

class OpenAIClient(BaseLLMClient):
    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        try:
            import openai as _openai  # type: ignore[import]
            self._openai = _openai
        except ImportError as exc:
            raise ImportError(
                "openai chua duoc cai. Chay: pip install openai"
            ) from exc

        api_key = config.api_key
        if not api_key:
            raise ValueError("Can OPENAI_API_KEY.")
        self._client = self._openai.OpenAI(api_key=api_key)
        log.info("OpenAIClient ready (model: %s).", config.model)

    def generate(self, system_prompt: str, user_message: str) -> str:
        for attempt in range(1, self.cfg.max_retries + 1):
            try:
                response = self._client.chat.completions.create(
                    model=self.cfg.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    max_completion_tokens=self.cfg.max_output_tokens,
                    temperature=self.cfg.temperature,
                )
                choice = response.choices[0]
                if choice.finish_reason == "length":
                    log.warning(
                        "Response bi cat vi length limit. "
                        "Tang RAM_WEAVER_MAX_OUTPUT_TOKENS."
                    )
                return choice.message.content or ""
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "OpenAI API loi (lan %d/%d): %s",
                    attempt, self.cfg.max_retries, exc,
                )
                if attempt < self.cfg.max_retries:
                    time.sleep(self.cfg.retry_delay * attempt)
                else:
                    raise
        return ""


# --------------------------------------------------------------------------- #
# Factory                                                                      #
# --------------------------------------------------------------------------- #

def create_client(config: LLMConfig) -> BaseLLMClient:
    """Tạo client đúng provider theo config."""
    if config.provider == "gemini":
        return GeminiClient(config)
    if config.provider == "openai":
        return OpenAIClient(config)
    raise ValueError(
        f"Provider khong ho tro: '{config.provider}'. Chon 'gemini' hoac 'openai'."
    )
