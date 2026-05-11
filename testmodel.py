#!/usr/bin/env python
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for p in [str(ROOT), str(ROOT / "llm")]:
    if p not in sys.path:
        sys.path.insert(0, p)

_env = ROOT / ".env"
if _env.is_file():
    for line in _env.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from config import LLMConfig
from llm.client import create_client

# Danh sách model cần test — thêm/bớt tùy bạn
MODELS = [
    ("gemini",      "gemini-2.5-flash",        "GEMINI_API_KEY"),
    # ("gemini",      "gemini-2.5-pro",           "GEMINI_API_KEY"),
    # ("openai",      "gpt-4o",                   "OPENAI_API_KEY"),
    # ("openai",      "o3",                       "OPENAI_API_KEY"),
    ("openrouter",  "google/gemma-3-27b-it", "OPENROUTER_API_KEY"),
    ("openrouter",  "google/gemma-3-12b-it", "OPENROUTER_API_KEY"),
    ("openrouter",  "google/gemma-3-4b-it",  "OPENROUTER_API_KEY"),
]

def test(provider: str, model: str, key_env: str) -> None:
    label = f"{provider}/{model}"

    # Bỏ qua nếu không có API key
    if not os.environ.get(key_env):
        print(f"  SKIP  {label}  (thiếu {key_env})")
        return

    try:
        os.environ["RAM_WEAVER_LLM_PROVIDER"] = provider
        os.environ["RAM_WEAVER_LLM_MODEL"] = model
        cfg = LLMConfig()
        llm = create_client(cfg)
        resp = llm.generate("You are a helpful assistant.", "Reply with exactly: OK")
        if "OK" in resp.upper():
            print(f"  OK    {label}")
        else:
            print(f"  FAIL  {label}  (phản hồi lạ: {repr(resp[:60])})")
    except Exception as e:
        print(f"  FAIL  {label}  ({e})")

print("Kiểm tra kết nối model...")
print("-" * 50)
for provider, model, key_env in MODELS:
    test(provider, model, key_env)
print("-" * 50)
print("Xong.")