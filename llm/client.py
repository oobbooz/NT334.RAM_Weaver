"""Unified LLM client – Google Gemini và OpenAI (flat layout).
Gemini 2.5-flash hỗ trợ tới 65536 output tokens – tăng default lên 8192.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
log = logging.getLogger("ram_weaver.llm.client")
from config import LLMConfig

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
    if config.provider == "huggingface":
        return HuggingFaceClient(config)
    if config.provider == "openrouter":
        return OpenRouterClient(config)
    raise ValueError(
        f"Provider khong ho tro: '{config.provider}'. "
        f"Chon 'gemini', 'openai', hoac 'huggingface' (cho Gemma local-free)."
    )


# --------------------------------------------------------------------------- #
# HuggingFace Inference API – Gemma 3 (paper Table 2, free tier)              #
# --------------------------------------------------------------------------- #

class HuggingFaceClient(BaseLLMClient):
    """Client cho HuggingFace Inference API (serverless, free tier).

    Dùng để chạy các model open-source như Google Gemma 3 được đánh giá
    trong paper Table 2, mà không cần GPU hay Ollama local.

    Setup:
        1. Đăng ký tài khoản miễn phí tại https://huggingface.co
        2. Vào Settings → Access Tokens → New token (role: Read)
        3. Set trong .env::

               RAM_WEAVER_LLM_PROVIDER=huggingface
               HF_API_TOKEN=hf_xxxxxxxxxxxxxxxx
               RAM_WEAVER_LLM_MODEL=google/gemma-3-27b-it

    Model names theo paper Table 2:
        * ``google/gemma-3-27b-it``  – Gemma 3 27b, EMR=40% trong paper
        * ``google/gemma-3-12b-it``  – Gemma 3 12b, EMR=0%  trong paper
        * ``google/gemma-3-4b-it``   – Gemma 3 4b,  EMR=30% trong paper

    Lưu ý: Free tier có rate limit ~1000 requests/ngày và timeout ~30s/request.
    """

    _BASE_URL = "https://router.huggingface.co/v1/chat/completions"

    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        import urllib.request as _urllib_request
        import json as _json
        self._urllib = _urllib_request
        self._json = _json

        self._token = config.api_key or ""
        if not self._token:
            raise ValueError(
                "Can HF_API_TOKEN. Set trong .env: HF_API_TOKEN=hf_xxx"
            )
        self._model = config.model or "google/gemma-3-27b-it"
        log.info("HuggingFaceClient ready (model: %s).", self._model)

    def generate(self, system_prompt: str, user_message: str) -> str:
        """Gọi HF Inference API chat completion endpoint."""
        url = self._BASE_URL
        # Gộp system prompt vào messages theo chuẩn chat template
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ]
        payload = self._json.dumps({
            "model": self._model,
            "messages": messages,
            "max_tokens": self.cfg.max_output_tokens,
            "temperature": self.cfg.temperature,
            "stream": False,
        }).encode("utf-8")

        for attempt in range(1, self.cfg.max_retries + 1):
            try:
                req = self._urllib.Request(
                    url,
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self._token}",
                    },
                    method="POST",
                )
                with self._urllib.urlopen(req, timeout=120) as resp:
                    body = self._json.loads(resp.read().decode("utf-8"))
                # OpenAI-compatible response format
                return body["choices"][0]["message"]["content"] or ""
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "HuggingFace API loi (lan %d/%d): %s",
                    attempt, self.cfg.max_retries, exc,
                )
                if attempt < self.cfg.max_retries:
                    time.sleep(self.cfg.retry_delay * attempt)
                else:
                    raise
        return ""

    @property
    def _base_url(self) -> str:
        return self._BASE_URL
class OpenRouterClient(BaseLLMClient):
        """Client cho OpenRouter – Gemma 3 27B/4B miễn phí ($0/M token).

        Setup:
            1. Đăng ký tại https://openrouter.ai
            2. Lấy API key miễn phí
            3. Set trong .env:
                RAM_WEAVER_LLM_PROVIDER=openrouter
                OPENROUTER_API_KEY=sk-or-xxxx
                RAM_WEAVER_LLM_MODEL=google/gemma-3-27b-it:free
        """

        def __init__(self, config: LLMConfig) -> None:
            super().__init__(config)
            try:
                import openai as _openai
                self._openai = _openai
            except ImportError as exc:
                raise ImportError("openai chua duoc cai. Chay: pip install openai") from exc

            api_key = config.api_key or ""
            if not api_key:
                raise ValueError("Can OPENROUTER_API_KEY trong .env")
            self._client = self._openai.OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
            )
            log.info("OpenRouterClient ready (model: %s).", config.model)

        def generate(self, system_prompt: str, user_message: str) -> str:
            for attempt in range(1, self.cfg.max_retries + 1):
                try:
                    response = self._client.chat.completions.create(
                        model=self.cfg.model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user",   "content": user_message},
                        ],
                        max_tokens=self.cfg.max_output_tokens,
                        temperature=self.cfg.temperature,
                    )
                    choice = response.choices[0]
                    if choice.finish_reason == "length":
                        log.warning("Response bi cat. Tang RAM_WEAVER_MAX_OUTPUT_TOKENS.")
                    return choice.message.content or ""
                except Exception as exc:
                    log.warning("OpenRouter API loi (lan %d/%d): %s", attempt, self.cfg.max_retries, exc)
                    if attempt < self.cfg.max_retries:
                        time.sleep(self.cfg.retry_delay * attempt)
                    else:
                        raise
            return ""