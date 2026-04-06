"""
RAM-Weaver: LLM-driven Reconstruction (Stage 2)
================================================
Sử dụng Google Gemini API để:
  Task A: High-Fidelity Text Restoration  — denoise & reconstruct message
  Task B: Contextual Forensic Querying    — trả lời câu hỏi điều tra

Yêu cầu:
  pip install google-genai
  Biến môi trường: GEMINI_API_KEY
"""

import os
import re
import json
import time
import logging
from dataclasses import dataclass
from typing import Optional

try:
    from google import genai
    from google.genai import types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("[WARNING] google-genai chưa được cài. Chạy: pip install google-genai")

log = logging.getLogger("LLM")


# ---------------------------------------------------------------------------
# Cấu hình
# ---------------------------------------------------------------------------

@dataclass
class LLMConfig:
    """Tham số cho Gemini API."""

    # Model name — Gemini 2.5 Pro theo paper (hoặc flash để tiết kiệm)
    model: str = "gemini-2.5-flash"

    # API key — đọc từ env var nếu không truyền trực tiếp
    api_key: Optional[str] = None

    # Số token tối đa trong response
    max_output_tokens: int = 4096

    # Temperature: thấp = consistent, cao = creative
    # Forensics cần consistency → dùng thấp
    temperature: float = 0.1

    # Số lần retry khi API lỗi
    max_retries: int = 3
    retry_delay: float = 2.0

    # Kích thước tối đa của input chunk gửi vào LLM (characters)
    # Gemini 2.5 Pro có context window 1M token → rất thoải mái
    max_input_chars: int = 500_000


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

RESTORE_SYSTEM_PROMPT = """You are an expert digital forensics analyst specializing in memory forensics and data reconstruction.

Your task is to reconstruct the original, clean user messages from noisy, fragmented memory data extracted from a messaging application (LINE Messenger).

STRICT RULES:
1. Extract and reconstruct ONLY the actual message text content (the "text" field values)
2. Remove ALL noise: duplicate fragments, system artifacts, metadata, JSON structure
3. Do NOT add, infer, hallucinate, or modify any content not present in the input
4. Preserve the EXACT original text including punctuation, capitalization, and special characters
5. If a message appears multiple times (memory duplicates), output it ONLY ONCE
6. Return ONLY the reconstructed message text, nothing else

Output format: The clean, reconstructed message text only."""


RESTORE_USER_TEMPLATE = """Below is a noisy, fragmented text chunk extracted from LINE Messenger process memory.

Reconstruct the original user message text:

--- MEMORY FRAGMENT START ---
{fragment}
--- MEMORY FRAGMENT END ---

Reconstructed message:"""


FORENSIC_QUERY_SYSTEM_PROMPT = """You are an expert digital forensics analyst. You have been given raw memory data extracted from LINE Messenger, a messaging application.

The data contains JSON-structured message objects with the following relevant fields:
- "text": the message content
- "from": sender's user ID  
- "to": recipient's user ID
- "createdTime": Unix timestamp in milliseconds
- "chatId": conversation ID
- "type": message type (1 = text message)
- "status": message status (1 = sent, 2 = delivered)

Your job is to analyze this data and answer the investigator's query accurately.

RULES:
1. Parse timestamps from Unix milliseconds to human-readable format (specify timezone if given)
2. Identify unique speakers from the "from" field
3. Filter messages by the criteria specified in the query
4. Present results in chronological order
5. Be precise and complete — missing information could affect a forensic investigation
6. If data is ambiguous or incomplete, note it explicitly"""


FORENSIC_QUERY_USER_TEMPLATE = """Raw memory data from LINE Messenger:

--- MEMORY DATA START ---
{memory_data}
--- MEMORY DATA END ---

Investigator's query: {query}

Forensic analysis:"""


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------

class GeminiClient:
    """
    Wrapper cho Google Gemini API.
    Xử lý authentication, retry, và rate limiting.
    """

    def __init__(self, config: Optional[LLMConfig] = None):
        self.cfg = config or LLMConfig()

        if not GEMINI_AVAILABLE:
            raise ImportError("Cài google-genai trước: pip install google-genai")

        # Lấy API key
        api_key = self.cfg.api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "Cần GEMINI_API_KEY. Set bằng:\n"
                "  export GEMINI_API_KEY='AIzaSyAlOzAz40-nhDG71GjPIgDeb2alHD8ENY4'\n"
                "  hoặc truyền vào LLMConfig(api_key='...')"
            )

        self.client = genai.Client(api_key=api_key)
        log.info(f"Gemini client khởi tạo thành công (model: {self.cfg.model})")

    def generate(self, system_prompt: str, user_message: str) -> str:
        """
        Gửi request đến Gemini API với retry logic.
        Trả về text response.
        """
        for attempt in range(self.cfg.max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.cfg.model,
                    contents=user_message,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        max_output_tokens=self.cfg.max_output_tokens,
                        temperature=self.cfg.temperature,
                    )
                )
                return response.text

            except Exception as e:
                log.warning(f"Gemini API lỗi (lần {attempt+1}/{self.cfg.max_retries}): {e}")
                if attempt < self.cfg.max_retries - 1:
                    time.sleep(self.cfg.retry_delay * (attempt + 1))
                else:
                    raise

        return ""


# ---------------------------------------------------------------------------
# Task A: High-Fidelity Text Restoration
# ---------------------------------------------------------------------------

class TextRestorer:
    """
    Nhận noisy memory chunks → gọi LLM → trả về reconstructed text.
    Tương ứng với Task A trong Stage 2 của RAM-Weaver.
    """

    def __init__(self, llm_client: GeminiClient, config: Optional[LLMConfig] = None):
        self.llm = llm_client
        self.cfg = config or LLMConfig()

    def restore(self, memory_fragment: str) -> str:
        """
        Restore một memory fragment thành clean text.

        Args:
            memory_fragment: Text chunk từ AMC output

        Returns:
            Reconstructed clean message text
        """
        # Truncate nếu quá dài
        if len(memory_fragment) > self.cfg.max_input_chars:
            log.warning(f"Fragment quá dài ({len(memory_fragment)} chars), truncate về {self.cfg.max_input_chars}")
            memory_fragment = memory_fragment[:self.cfg.max_input_chars]

        user_msg = RESTORE_USER_TEMPLATE.format(fragment=memory_fragment)

        log.debug(f"Gửi fragment {len(memory_fragment)} chars đến Gemini để restore...")
        result = self.llm.generate(RESTORE_SYSTEM_PROMPT, user_msg)

        return result.strip()

    def restore_batch(self, chunks: list[str]) -> list[str]:
        """
        Restore nhiều chunks, trả về list kết quả tương ứng.
        """
        results = []
        for i, chunk in enumerate(chunks):
            log.info(f"Đang restore chunk {i+1}/{len(chunks)}...")
            restored = self.restore(chunk)
            results.append(restored)
            # Rate limiting
            time.sleep(0.5)
        return results

    def restore_from_file(self, chunks_file: str) -> list[str]:
        # Sửa lại toàn bộ nội dung bên trong hàm này
        with open(chunks_file, "r", encoding="utf-8") as f:
            content = f.read()

        log.info(f"Đang gộp toàn bộ dữ liệu ({len(content)} chars) vào 1 request duy nhất...")
        
        user_msg = f"""Dưới đây là TOÀN BỘ các đoạn memory fragment trích xuất được từ LINE Messenger.
        Hãy đọc tất cả, loại bỏ nhiễu, sắp xếp theo thời gian (createdTime) và phục hồi lại danh sách tin nhắn gốc:
        
        --- MEMORY DATA START ---
        {content}
        --- MEMORY DATA END ---
        """

        # Gửi đúng 1 lần duy nhất
        result = self.llm.generate(RESTORE_SYSTEM_PROMPT, user_msg)
        return [result.strip()]


# ---------------------------------------------------------------------------
# Task B: Contextual Forensic Querying
# ---------------------------------------------------------------------------

class ForensicQueryEngine:
    """
    Cho phép điều tra viên đặt câu hỏi bằng ngôn ngữ tự nhiên về
    nội dung memory dump đã qua AMC.

    Ví dụ query (theo Figure 2 trong paper):
    - "List all messages after Wed Jul 02 2025 14:15:22 (Taipei time)"
    - "Who sent the most messages?"
    - "What was discussed about topic X?"
    - "Find all messages containing keyword Y"
    """

    def __init__(self, llm_client: GeminiClient, config: Optional[LLMConfig] = None):
        self.llm = llm_client
        self.cfg = config or LLMConfig()
        self.memory_data: str = ""

    def load_memory_data(self, chunks_file: str):
        """Load AMC output file vào engine để query."""
        with open(chunks_file, "r", encoding="utf-8") as f:
            self.memory_data = f.read()
        log.info(f"Đã load {len(self.memory_data)} chars vào ForensicQueryEngine")

    def load_memory_string(self, memory_data: str):
        """Load memory data trực tiếp từ string."""
        self.memory_data = memory_data

    def query(self, investigator_query: str) -> str:
        """
        Đặt câu hỏi điều tra về memory data.

        Args:
            investigator_query: Câu hỏi bằng ngôn ngữ tự nhiên

        Returns:
            Kết quả phân tích từ LLM
        """
        if not self.memory_data:
            raise ValueError("Chưa load memory data. Gọi load_memory_data() trước.")

        # Truncate nếu cần
        data = self.memory_data
        if len(data) > self.cfg.max_input_chars:
            log.warning(f"Memory data quá lớn ({len(data)} chars), truncate...")
            data = data[:self.cfg.max_input_chars]

        user_msg = FORENSIC_QUERY_USER_TEMPLATE.format(
            memory_data=data,
            query=investigator_query
        )

        log.info(f"Forensic query: '{investigator_query}'")
        result = self.llm.generate(FORENSIC_QUERY_SYSTEM_PROMPT, user_msg)
        return result.strip()

    def interactive_session(self):
        """
        Chế độ interactive: điều tra viên nhập query liên tục.
        Nhập 'exit' hoặc 'quit' để thoát.
        """
        if not self.memory_data:
            print("Lỗi: Chưa load memory data!")
            return

        print("\n" + "="*60)
        print("RAM-Weaver Interactive Forensic Query Session")
        print("Memory data đã sẵn sàng. Nhập câu hỏi điều tra.")
        print("Gõ 'exit' để thoát.")
        print("="*60 + "\n")

        history = []

        while True:
            try:
                query = input("🔍 Query: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nKết thúc session.")
                break

            if query.lower() in ("exit", "quit", "q"):
                print("Kết thúc session.")
                break

            if not query:
                continue

            print("\n⏳ Đang phân tích...\n")
            try:
                answer = self.query(query)
                print("📋 Kết quả:")
                print("-" * 40)
                print(answer)
                print("-" * 40 + "\n")

                history.append({"query": query, "answer": answer})

            except Exception as e:
                print(f"❌ Lỗi: {e}\n")

        # Lưu history
        if history:
            history_path = "./output/query_history.json"
            os.makedirs("./output", exist_ok=True)
            with open(history_path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            print(f"Query history đã lưu tại: {history_path}")


# ---------------------------------------------------------------------------
# Entry point: Full Stage 2 Pipeline
# ---------------------------------------------------------------------------

class LLMReconstructor:
    """
    Orchestrate toàn bộ Stage 2:
    - Chọn task (restore / query / cả hai)
    - Gọi GeminiClient
    - Trả về kết quả
    """

    def __init__(self, config: Optional[LLMConfig] = None):
        self.cfg = config or LLMConfig()
        self.llm = GeminiClient(self.cfg)
        self.restorer = TextRestorer(self.llm, self.cfg)
        self.query_engine = ForensicQueryEngine(self.llm, self.cfg)

    def run_restoration(self, chunks_file: str, output_file: str = "./output/restored.txt") -> list[str]:
        """
        Chạy Task A: Restore text từ AMC output file.
        Lưu kết quả ra output_file.
        """
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
        """
        Chạy Task B: Forensic query một lần.
        """
        self.query_engine.load_memory_data(chunks_file)
        return self.query_engine.query(query)

    def run_interactive(self, chunks_file: str):
        """
        Chạy Task B: Interactive query session.
        """
        self.query_engine.load_memory_data(chunks_file)
        self.query_engine.interactive_session()


# ---------------------------------------------------------------------------
# Chạy thử (standalone test)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("RAM-Weaver - Stage 2: LLM Reconstruction")
    print("Sử dụng Google Gemini API\n")

    # Kiểm tra API key
    if not os.environ.get("GEMINI_API_KEY"):
        print("Cần set GEMINI_API_KEY:")
        print("  export GEMINI_API_KEY='AIzaSyAlOzAz40-nhDG71GjPIgDeb2alHD8ENY4'")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage:")
        print("  Restoration : python llm_reconstructor.py restore <chunks_file>")
        print("  Forensic Q  : python llm_reconstructor.py query <chunks_file> '<question>'")
        print("  Interactive : python llm_reconstructor.py interactive <chunks_file>")
        sys.exit(1)

    mode = sys.argv[1]
    chunks_file = sys.argv[2] if len(sys.argv) > 2 else "./output/amc_output.txt"

    config = LLMConfig(
        model="gemini-2.5-flash",
        temperature=0.1,
    )
    reconstructor = LLMReconstructor(config)

    if mode == "restore":
        results = reconstructor.run_restoration(chunks_file)
        print(f"\n✓ Restored {len(results)} messages")

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
