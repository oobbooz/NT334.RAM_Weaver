import os
import sys
from dataclasses import dataclass, field


@dataclass
class AMCConfig:
    volatility_path: str | None = None
    python_executable: str | None = None
    vad_dump_dir: str = "./dumps/vad_raw"
    output_dir: str = "./output/amc_chunks"
    encodings: list = field(default_factory=lambda: ["utf-8", "utf-16-le"])
    min_string_len: int = 8
    extraction_mode: str = "auto"
    json_keys_of_interest: list = field(default_factory=lambda: [
        "text", "from", "to", "createdTime", "chatId",
        "contentType", "status", "type", "id"
    ])
    json_key_threshold: int = 2
    noise_patterns: list = field(default_factory=lambda: [
        r"[A-Za-z]:\\[\w\\/. -]+",
        r"\{[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\}",
        r"https?://[^\s\"'<>]{5,200}",
        r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}",
        r"[A-Za-z0-9+/]{40,}={0,2}",
        r"\\x[0-9a-fA-F]{2}",
        r"\x00+",
    ])

    def __post_init__(self) -> None:
        if self.volatility_path is None:
            self.volatility_path = os.environ.get("RAM_WEAVER_VOL_PATH")
        if self.python_executable is None:
            self.python_executable = os.environ.get("RAM_WEAVER_PYTHON") or sys.executable
        self.vad_dump_dir = os.environ.get("RAM_WEAVER_VAD_DUMP_DIR", self.vad_dump_dir)
        self.output_dir = os.environ.get("RAM_WEAVER_OUTPUT_DIR", self.output_dir)
        self.extraction_mode = os.environ.get("RAM_WEAVER_EXTRACTION_MODE", self.extraction_mode)
