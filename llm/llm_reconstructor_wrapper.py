
import os
import sys
from pathlib import Path

try:
    from .config import LLMConfig
    from .pipeline import LLMReconstructor
except ImportError:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from llm.config import LLMConfig
    from llm.pipeline import LLMReconstructor


def main() -> None:
    print("RAM-Weaver - Stage 2: LLM Reconstruction")
    print("Sử dụng Google Gemini API\n")

    if not os.environ.get("GEMINI_API_KEY"):
        print("Cần set GEMINI_API_KEY:")
        print("  export GEMINI_API_KEY='your-key'")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage:")
        print("  Restoration : python llm_reconstructor_wrapper.py restore <chunks_file>")
        print("  Forensic Q  : python llm_reconstructor_wrapper.py query <chunks_file> '<question>'")
        print("  Interactive : python llm_reconstructor_wrapper.py interactive <chunks_file>")
        sys.exit(1)

    mode = sys.argv[1]
    chunks_file = sys.argv[2] if len(sys.argv) > 2 else "./output/amc_output.txt"

    config = LLMConfig(temperature=0.1)
    reconstructor = LLMReconstructor(config)

    if mode == "restore":
        results = reconstructor.run_restoration(chunks_file)
        print(f"\nRestored {len(results)} messages")
    elif mode == "query":
        query = sys.argv[3] if len(sys.argv) > 3 else "List all messages in chronological order"
        answer = reconstructor.run_forensic_query(chunks_file, query)
        print(f"\nQuery: {query}")
        print(f"\nAnswer:\n{answer}")
    elif mode == "interactive":
        reconstructor.run_interactive(chunks_file)
    else:
        print(f"Mode không hợp lệ: {mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
