import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class LLMConfig:
    model: Optional[str] = None
    api_key: Optional[str] = None
    max_output_tokens: int = 4096
    temperature: float = 0.1
    max_retries: int = 3
    retry_delay: float = 2.0
    max_input_chars: int = 500_000

    def __post_init__(self) -> None:
        if self.model is None:
            self.model = os.environ.get("RAM_WEAVER_GEMINI_MODEL", "gemini-2.5-flash")
