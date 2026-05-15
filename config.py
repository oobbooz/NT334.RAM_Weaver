"""Cấu hình cho cả hai giai đoạn của RAM-Weaver (flat layout).

AMCConfig (Giai đoạn 1) + LLMConfig (Giai đoạn 2) trong một file duy nhất.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# =============================================================================
# Nạp .env (thư mục gốc project)
# =============================================================================

_PROJECT_ROOT = Path(__file__).resolve().parent
_DEFAULT_ENV_FILE = _PROJECT_ROOT / ".env"


def _getenv(name: str, default: str | None = None) -> str | None:
    """Lấy biến môi trường; coi chuỗi rỗng/toàn khoảng trắng là chưa set."""
    value = os.environ.get(name)
    if value is None:
        return default
    if isinstance(value, str) and value.strip() == "":
        return default
    return value


def load_env(env_file: str | os.PathLike[str] | None = None, *, override: bool = False) -> Path | None:
    """Nạp các cặp `KEY=VALUE` từ file .env vào biến môi trường.

    - Mặc định dùng `<thu_muc_goc>/.env`.
    - Mặc định KHÔNG ghi đè biến môi trường đã có (dùng `setdefault`).
    - Hỗ trợ dòng kiểu `export KEY=...` và dấu nháy đơn/đôi đơn giản.
    """
    path = Path(env_file) if env_file is not None else _DEFAULT_ENV_FILE
    try:
        if not path.is_file():
            return None
        for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if not key:
                continue
            value = value.strip().strip('"').strip("'")
            if override:
                os.environ[key] = value
            else:
                os.environ.setdefault(key, value)
        return path
    except Exception:
        # Best-effort: không làm import fail chỉ vì parse .env.
        return None


    # Tự nạp .env khi import để mọi entrypoint hành xử nhất quán.
load_env()


# =============================================================================
# Giai đoạn 1 – Adaptive Memory Carver
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
        r"[A-Za-z]:\\[\w\\/. -]+",                          # Đường dẫn kiểu Windows
        r"\{[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\}",  # GUID
        r"https?://[^\s\"'<>]{5,200}",                      # URL
        r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}",                  # Địa chỉ IPv4
        r"[A-Za-z0-9+/]{40,}={0,2}",                        # Base64 blob ≥ 40 ký tự
        r"\\x[0-9a-fA-F]{2}",                               # Literal hex escape
        # Ghi chú: bỏ pattern null-byte (\x00+) vì bước trích string đã lọc
        # byte không in được, nên null thường không đi tới bước này.
    ])

    def __post_init__(self) -> None:
        if self.volatility_path is None:
            self.volatility_path = _getenv("RAM_WEAVER_VOL_PATH")
        if self.python_executable is None:
            self.python_executable = (
                _getenv("RAM_WEAVER_PYTHON") or sys.executable
            )
        self.vad_dump_dir = _getenv("RAM_WEAVER_VAD_DUMP_DIR", self.vad_dump_dir) or self.vad_dump_dir
        self.output_dir = _getenv("RAM_WEAVER_OUTPUT_DIR", self.output_dir) or self.output_dir
        self.extraction_mode = _getenv("RAM_WEAVER_EXTRACTION_MODE", self.extraction_mode) or self.extraction_mode


# =============================================================================
# Giai đoạn 2 – Tái hiện dựa trên LLM
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
        self.provider = (_getenv("RAM_WEAVER_LLM_PROVIDER", self.provider) or self.provider).lower()

        if self.model is None:
            env_model = (
                _getenv("RAM_WEAVER_LLM_MODEL")
                or _getenv("RAM_WEAVER_GEMINI_MODEL")
            )
            self.model = env_model or (
                "o3" if self.provider == "openai" else "gemini-2.5-flash"  # dùng Flash (Pro cần billing riêng)
            )

        if self.api_key is None:
            if self.provider == "openai":
                self.api_key = os.environ.get("OPENAI_API_KEY")
            elif self.provider == "openrouter":
                self.api_key = os.environ.get("OPENROUTER_API_KEY")
            else:
                self.api_key = os.environ.get("GEMINI_API_KEY")

        # Cho phép override từ env
        env_tokens = _getenv("RAM_WEAVER_MAX_OUTPUT_TOKENS")
        if env_tokens:
            try:
                self.max_output_tokens = int(env_tokens)
            except ValueError:
                pass

        # [EDITED]: Bổ sung tính năng override max_input_chars từ biến môi trường .env
        env_chars = _getenv("RAM_WEAVER_MAX_INPUT_CHARS")
        if env_chars:
            try:
                self.max_input_chars = int(env_chars)
            except ValueError:
                pass
