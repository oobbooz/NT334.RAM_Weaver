"""High-Fidelity Text Restoration – Stage 2, Task A.

The paper evaluates two restoration strategies:
- **Single-chunk restore** – each chunk sent individually (good for isolation).
- **Batch restore** – all chunks merged into a single LLM call for holistic
  deduplication and chronological sorting.

The paper uses the batch approach (all memory data in one request) for the
LINE Messenger case study, so :meth:`restore_from_file` is the primary path.
"""

from __future__ import annotations

import logging
import time

from client import BaseLLMClient
from config import LLMConfig
from prompts import (
    RESTORE_BATCH_USER_TEMPLATE,
    RESTORE_SYSTEM_PROMPT,
    RESTORE_USER_TEMPLATE,
)

log = logging.getLogger("ram_weaver.llm.restorer")

_CHUNK_SLEEP_SECONDS = 0.5  # throttle between per-chunk API calls


class TextRestorer:
    """Restores clean message text from noisy memory fragments via an LLM.

    Args:
        llm_client: Provider-agnostic LLM client instance.
        config:     LLM configuration (used for token/char limits).
    """

    def __init__(
        self,
        llm_client: BaseLLMClient,
        config: LLMConfig | None = None,
    ) -> None:
        self.llm = llm_client
        self.cfg = config or LLMConfig()

    # ------------------------------------------------------------------ #
    # Primary path – batch restore (recommended)                          #
    # ------------------------------------------------------------------ #

    def restore_from_file(self, chunks_file: str) -> list[str]:
        """Read a chunk file and restore all fragments in a single LLM call.

        This is the approach used in the paper: the entire AMC output is sent
        to the LLM at once so it can deduplicate across chunks and sort by
        ``createdTime``.

        Args:
            chunks_file: Path to the AMC output file (chunks separated by
                         ``---CHUNK---``).

        Returns:
            List containing the single restored text block.
        """
        with open(chunks_file, "r", encoding="utf-8") as fh:
            content = fh.read()

        log.info(
            "Batch restore: %d chars from '%s'.", len(content), chunks_file
        )

        if len(content) > self.cfg.max_input_chars:
            log.warning(
                "Content (%d chars) exceeds max_input_chars (%d). Truncating.",
                len(content), self.cfg.max_input_chars,
            )
            content = content[: self.cfg.max_input_chars]

        user_msg = RESTORE_BATCH_USER_TEMPLATE.format(content=content)
        result = self.llm.generate(RESTORE_SYSTEM_PROMPT, user_msg) or ""
        return [result.strip()]

    # ------------------------------------------------------------------ #
    # Alternative path – per-chunk restore                                #
    # ------------------------------------------------------------------ #

    def restore(self, memory_fragment: str) -> str:
        """Restore a single memory fragment.

        Args:
            memory_fragment: Raw text chunk from the AMC output.

        Returns:
            Cleaned, reconstructed message text.
        """
        if len(memory_fragment) > self.cfg.max_input_chars:
            log.warning(
                "Fragment (%d chars) exceeds max_input_chars. Truncating.",
                len(memory_fragment),
            )
            memory_fragment = memory_fragment[: self.cfg.max_input_chars]

        user_msg = RESTORE_USER_TEMPLATE.format(fragment=memory_fragment)
        log.debug("Restoring fragment (%d chars)…", len(memory_fragment))
        return (self.llm.generate(RESTORE_SYSTEM_PROMPT, user_msg) or "").strip()

    def restore_batch(self, chunks: list[str]) -> list[str]:
        """Restore each chunk independently and return a parallel list of results.

        Useful for evaluation (S2 in the paper) where per-message accuracy
        metrics (EMR, CER) are computed.

        Args:
            chunks: List of raw memory chunks.

        Returns:
            List of restored strings, one per input chunk.
        """
        results: list[str] = []
        total = len(chunks)
        for i, chunk in enumerate(chunks, start=1):
            log.info("Restoring chunk %d / %d …", i, total)
            results.append(self.restore(chunk))
            if i < total:
                time.sleep(_CHUNK_SLEEP_SECONDS)
        return results
