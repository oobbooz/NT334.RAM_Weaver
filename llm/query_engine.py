import json
import logging

from .client import GeminiClient
from .config import LLMConfig
from .prompts import FORENSIC_QUERY_SYSTEM_PROMPT, FORENSIC_QUERY_USER_TEMPLATE

log = logging.getLogger("LLM")


class ForensicQueryEngine:
    def __init__(self, llm_client: GeminiClient, config: LLMConfig | None = None):
        self.llm = llm_client
        self.cfg = config or LLMConfig()
        self.memory_data: str = ""

    def load_memory_data(self, chunks_file: str):
        with open(chunks_file, "r", encoding="utf-8") as f:
            self.memory_data = f.read()
        log.info(f"Đã load {len(self.memory_data)} chars vào ForensicQueryEngine")

    def load_memory_string(self, memory_data: str):
        self.memory_data = memory_data

    def query(self, investigator_query: str) -> str:
        if not self.memory_data:
            raise ValueError("Chưa load memory data. Gọi load_memory_data() trước.")
        data = self.memory_data
        if len(data) > self.cfg.max_input_chars:
            log.warning(f"Memory data quá lớn ({len(data)} chars), truncate...")
            data = data[:self.cfg.max_input_chars]
        user_msg = FORENSIC_QUERY_USER_TEMPLATE.format(memory_data=data, query=investigator_query)
        log.info(f"Forensic query: '{investigator_query}'")
        return self.llm.generate(FORENSIC_QUERY_SYSTEM_PROMPT, user_msg).strip()

    def interactive_session(self):
        if not self.memory_data:
            print("Lỗi: Chưa load memory data!")
            return
        print("\n" + "=" * 60)
        print("RAM-Weaver Interactive Forensic Query Session")
        print("Memory data đã sẵn sàng. Nhập câu hỏi điều tra.")
        print("Gõ 'exit' để thoát.")
        print("=" * 60 + "\n")
        history = []
        while True:
            try:
                query = input("Query: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nKết thúc session.")
                break
            if query.lower() in ("exit", "quit", "q"):
                print("Kết thúc session.")
                break
            if not query:
                continue
            print("\nDang phan tich...\n")
            try:
                answer = self.query(query)
                print("Ket qua:")
                print("-" * 40)
                print(answer)
                print("-" * 40 + "\n")
                history.append({"query": query, "answer": answer})
            except Exception as e:
                print(f"Loi: {e}\n")
        if history:
            history_path = "./output/query_history.json"
            import os
            os.makedirs("./output", exist_ok=True)
            with open(history_path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            print(f"Query history đã lưu tại: {history_path}")
