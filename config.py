"""Cấu hình cho cả hai stage của RAM-Weaver (flat layout).

AMCConfig (Stage 1) + LLMConfig (Stage 2) trong một file duy nhất.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Optional


# =============================================================================
# Stage 1 – Adaptive Memory Carver
# =============================================================================

@dataclass
class AMCConfig:
    volatility_path: str | None = None
    python_executable: str | None = None
    volatility_timeout: int = 300
    vad_dump_dir: str = "./vad_dumps"
    output_dir: str = "./output_s3"
    extraction_mode: str = "auto"
    encodings: list[str] = field(
        default_factory=lambda: ["utf-8", "utf-16-le"]
    )
    min_string_len: int = 8
    json_keys_of_interest: list[str] = field(default_factory=lambda: [
        "text", "from", "to", "createdTime", "chatId",
        "contentType", "status", "type", "id",
    ])
    json_key_threshold: int = 2
    noise_patterns: list[str] = field(default_factory=lambda: [
        r"[A-Za-z]:\\[\w\\/. -]+",                          # Windows file paths
        r"\{[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\}",  # GUIDs
        r"https?://[^\s\"'<>]{5,200}",                      # URLs
        r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}",                  # IPv4 addresses
        r"[A-Za-z0-9+/]{40,}={0,2}",                        # base64 blobs ≥40 chars
        r"\\x[0-9a-fA-F]{2}",                               # escaped hex literals
        # Note: null-byte pattern (\x00+) was removed – string extraction
        # already filters non-printable bytes, so nulls never reach this stage.
    ])

    def __post_init__(self) -> None:
        if self.volatility_path is None:
            self.volatility_path = os.environ.get("RAM_WEAVER_VOL_PATH")
        if self.python_executable is None:
            self.python_executable = (
                os.environ.get("RAM_WEAVER_PYTHON") or sys.executable
            )
        self.vad_dump_dir = os.environ.get(
            "RAM_WEAVER_VAD_DUMP_DIR", self.vad_dump_dir
        )
        self.output_dir = os.environ.get(
            "RAM_WEAVER_OUTPUT_DIR", self.output_dir
        )
        self.extraction_mode = os.environ.get(
            "RAM_WEAVER_EXTRACTION_MODE", self.extraction_mode
        )


# =============================================================================
# Stage 2 – LLM-driven Reconstruction
# =============================================================================

@dataclass
class LLMConfig:
    provider: str = "gemini"
    model: Optional[str] = None
    api_key: Optional[str] = None

    # Tăng lên 16384 để tránh truncation khi LLM phân tích nhiều tin nhắn.
    # Gemini 2.5-flash hỗ trợ tối đa 65536 output tokens.
    # Ghi đè bằng RAM_WEAVER_MAX_OUTPUT_TOKENS trong .env nếu cần.
    max_output_tokens: int = 16384

    temperature: float = 0.1
    max_retries: int = 3
    retry_delay: float = 2.0
    
    # [EDITED]: Tăng mặc định từ 500_000 lên 2_000_000 để tránh việc input data bị cắt (truncating) 
    # khi xử lý file AMC lớn. Ví dụ: line_s3.dmp tạo ra ~618KB dữ liệu.
    max_input_chars: int = 2_000_000

    def __post_init__(self) -> None:
        self.provider = os.environ.get(
            "RAM_WEAVER_LLM_PROVIDER", self.provider
        ).lower()

        if self.model is None:
            env_model = (
                os.environ.get("RAM_WEAVER_LLM_MODEL")
                or os.environ.get("RAM_WEAVER_GEMINI_MODEL")
            )
            self.model = env_model or (
                "o3" if self.provider == "openai" else "gemini-2.5-flash"  # dùng Flash (Pro cần billing riêng)
            )

        if self.api_key is None:
            if self.provider == "openai":
                self.api_key = os.environ.get("OPENAI_API_KEY")
            elif self.provider == "huggingface":
                self.api_key = os.environ.get("HF_API_TOKEN")
            elif self.provider == "openrouter":
                self.api_key = os.environ.get("OPENROUTER_API_KEY")
            else:
                self.api_key = os.environ.get("GEMINI_API_KEY")

        # Cho phép override từ env
        env_tokens = os.environ.get("RAM_WEAVER_MAX_OUTPUT_TOKENS")
        if env_tokens:
            try:
                self.max_output_tokens = int(env_tokens)
            except ValueError:
                pass

        # [EDITED]: Bổ sung tính năng override max_input_chars từ biến môi trường .env
        env_chars = os.environ.get("RAM_WEAVER_MAX_INPUT_CHARS")
        if env_chars:
            try:
                self.max_input_chars = int(env_chars)
            except ValueError:
                pass
