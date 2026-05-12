"""Stage 2 top-level pipeline: LLM-driven Reconstruction.

Orchestrates Task A (High-Fidelity Restoration) and Task B (Contextual
Forensic Querying) using the provider-agnostic LLM client factory.

Usage::

    from ram_weaver.llm import LLMConfig, LLMReconstructor

    rec = LLMReconstructor(LLMConfig(provider="openai"))
    results = rec.run_restoration("./output/amc_output.txt")
    answer  = rec.run_forensic_query("./output/amc_output.txt",
                                     "List all messages after 14:15 Taipei time.")
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from client import BaseLLMClient, create_client
from query_engine import ForensicQueryEngine
from restorer import TextRestorer
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LLMConfig
log = logging.getLogger("ram_weaver.llm.pipeline")

_DEFAULT_RESTORED_OUTPUT = "./output/restored.txt"


class LLMReconstructor:
    """End-to-end Stage 2 orchestrator.

    Args:
        config:     LLM configuration.  Defaults to ``LLMConfig()`` which
                    reads provider and key from environment variables.
        llm_client: Pre-built client (useful for testing / dependency
                    injection).  If supplied, ``config`` is ignored for
                    client construction but still used for limits.
    """

    def __init__(
        self,
        config: Optional[LLMConfig] = None,
        llm_client: Optional[BaseLLMClient] = None,
    ) -> None:
        self.cfg = config or LLMConfig()
        self.llm: BaseLLMClient = llm_client or create_client(self.cfg)
        self.restorer = TextRestorer(self.llm, self.cfg)
        self.query_engine = ForensicQueryEngine(self.llm, self.cfg)

    # ------------------------------------------------------------------ #
    # Task A – High-Fidelity Restoration                                  #
    # ------------------------------------------------------------------ #

    def run_restoration(
        self,
        chunks_file: str,
        output_file: str = _DEFAULT_RESTORED_OUTPUT,
    ) -> list[str]:
        """Restore message text from AMC output and persist results.

        Args:
            chunks_file: Path to the AMC chunk file (Stage 1 output).
            output_file: Path where restored text is written.

        Returns:
            List of restored message strings.
        """
        log.info("=" * 60)
        log.info("Stage 2 – Task A: High-Fidelity Text Restoration")
        log.info("=" * 60)

        results = self.restorer.restore_from_file(chunks_file)

        os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as fh:
            for i, text in enumerate(results, start=1):
                fh.write(f"=== Restored Block {i} ===\n{text}\n\n")

        log.info("Restoration complete.  Output: %s", output_file)
        return results

    # ------------------------------------------------------------------ #
    # Task B – Contextual Forensic Querying                               #
    # ------------------------------------------------------------------ #

    def run_forensic_query(self, chunks_file: str, query: str) -> str:
        """Answer a single forensic query against the AMC output.

        Args:
            chunks_file: Path to the AMC chunk file.
            query:       Natural-language investigation question.

        Returns:
            LLM-generated forensic analysis string.
        """
        log.info("=" * 60)
        log.info("Stage 2 – Task B: Forensic Query")
        log.info("Query: %s", query)
        log.info("=" * 60)

        self.query_engine.load_memory_file(chunks_file)
        return self.query_engine.query(query)

    # ------------------------------------------------------------------ #
    # Interactive session                                                  #
    # ------------------------------------------------------------------ #

    def run_interactive(self, chunks_file: str) -> None:
        """Start an interactive forensic query REPL.

        Args:
            chunks_file: Path to the AMC chunk file.
        """
        self.query_engine.load_memory_file(chunks_file)
        self.query_engine.interactive_session()
