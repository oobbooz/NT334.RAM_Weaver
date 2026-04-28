from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class LLMConfig:
    provider: str = "gemini"
    model: Optional[str] = None
    api_key: Optional[str] = None

    max_output_tokens: int = 16384
    temperature: float = 0.1

    max_retries: int = 3
    retry_delay: float = 2.0

    max_input_chars: int = 500_000

    def __post_init__(self) -> None:
        self.provider = os.environ.get(
            "RAM_WEAVER_LLM_PROVIDER", self.provider
        ).lower()

        # model auto select
        if self.model is None:
            self.model = (
                os.environ.get("RAM_WEAVER_LLM_MODEL")
                or os.environ.get("RAM_WEAVER_GEMINI_MODEL")
                or (
                    "o3"
                    if self.provider == "openai"
                    else "gemini-2.5-flash"
                )
            )

        # api key auto select
        if self.api_key is None:
            self.api_key = os.environ.get(
                "OPENAI_API_KEY"
                if self.provider == "openai"
                else "GEMINI_API_KEY"
            )

        # allow override tokens
        env_tokens = os.environ.get("RAM_WEAVER_MAX_OUTPUT_TOKENS")
        if env_tokens:
            try:
                self.max_output_tokens = int(env_tokens)
            except ValueError:
                pass
