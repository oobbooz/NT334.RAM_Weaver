from typing import Optional

from .client import GeminiClient
from .config import LLMConfig
from .query_engine import ForensicQueryEngine
from .restorer import TextRestorer


class LLMReconstructor:
    def __init__(self, config: Optional[LLMConfig] = None):
        self.cfg = config or LLMConfig()
        self.llm = GeminiClient(self.cfg)
        self.restorer = TextRestorer(self.llm, self.cfg)
        self.query_engine = ForensicQueryEngine(self.llm, self.cfg)

    def run_restoration(self, chunks_file: str, output_file: str = "./output/restored.txt") -> list[str]:
        import logging, os
        log = logging.getLogger("LLM")
        log.info("=" * 60)
        log.info("Stage 2 - Task A: Text Restoration")
        log.info("=" * 60)
        results = self.restorer.restore_from_file(chunks_file)
        os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            for i, r in enumerate(results):
                f.write(f"=== Message {i+1} ===\n{r}\n\n")
        log.info(f"Kết quả restoration lưu tại: {output_file}")
        return results

    def run_forensic_query(self, chunks_file: str, query: str) -> str:
        self.query_engine.load_memory_data(chunks_file)
        return self.query_engine.query(query)

    def run_interactive(self, chunks_file: str):
        self.query_engine.load_memory_data(chunks_file)
        self.query_engine.interactive_session()
