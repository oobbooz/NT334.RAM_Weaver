#!/usr/bin/env python
"""Điểm vào CLI cho Giai đoạn 2 – Khôi phục dựa trên LLM (flat layout).

Tất cả module (pipeline.py, client.py, restorer.py, ...) nằm cùng thư mục.

Lệnh con:
    restore     – Task A: khôi phục text từ memory chunks.
    query       – Task B: truy vấn forensic một lần.
    interactive – Task B: phiên REPL tương tác.

Cách dùng:
    python llm_runner.py restore  <chunks_file>
    python llm_runner.py query    <chunks_file> "<question>"
    python llm_runner.py interactive <chunks_file>
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))

    # Load .env ở project root (cùng cấp với config.py)
    project_root = script_dir.parent
    from config import load_env  # noqa: PLC0415
    load_env(project_root / ".env")

    if len(sys.argv) < 3:
        print(
            "Cách dùng:\n"
            "  python llm_runner.py restore     <chunks_file>\n"
            "  python llm_runner.py query        <chunks_file> '<question>'\n"
            "  python llm_runner.py interactive  <chunks_file>"
        )
        sys.exit(1)

    mode = sys.argv[1].lower()
    chunks_file = sys.argv[2]

    if not os.path.isfile(chunks_file):
        print(f"[ERROR] Khong tim thay chunks file: {chunks_file}")
        sys.exit(1)

    # Validate API key trước khi import SDK nặng
    provider = os.environ.get("RAM_WEAVER_LLM_PROVIDER", "gemini").lower()
    if provider == "openai":
        key_var = "OPENAI_API_KEY"
    elif provider == "openrouter":
        key_var = "OPENROUTER_API_KEY"
    else:
        key_var = "GEMINI_API_KEY"
    if not os.environ.get(key_var):
        print(f"[ERROR] Chua set {key_var}. Them vao .env hoac export truoc khi chay.")
        sys.exit(1)

    # Import sau khi sys.path đã được set
    from config import LLMConfig          
    from llm_pipeline import LLMReconstructor  

    config = LLMConfig(temperature=0.1)
    rec = LLMReconstructor(config)

    if mode == "restore":
        results = rec.run_restoration(chunks_file)
        print(f"\nKhoi phuc xong: {len(results)} block(s).")

    elif mode == "query":
        if len(sys.argv) < 4:
            print("[ERROR] Thieu query text.\n"
                "Cách dùng: python llm_runner.py query <chunks_file> '<question>'")
            sys.exit(1)
        question = sys.argv[3]
        answer = rec.run_forensic_query(chunks_file, question)
        print(f"\nQuery  : {question}")
        print(f"\nAnswer :\n{answer}")

    elif mode == "interactive":
        rec.run_interactive(chunks_file)

    else:
        print(f"[ERROR] Mode khong hop le: '{mode}'. Chon: restore | query | interactive")
        sys.exit(1)


if __name__ == "__main__":
    main()
