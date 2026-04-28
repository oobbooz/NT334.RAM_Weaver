"""Stage 1 top-level pipeline: Adaptive Memory Carver (AMC).

Orchestrates the two AMC sub-stages:
    1. Adaptive Memory Extraction (AME) via :class:`AdaptiveMemoryExtractor`.
    2. Targeted Artifact Filtering via :class:`ArtifactFilter`.

Usage::

    from ram_weaver.amc import AMCConfig, AdaptiveMemoryCarver

    amc = AdaptiveMemoryCarver(AMCConfig())
    output_path = amc.run("/path/to/memory.vmem", pid=1234)
"""

from __future__ import annotations

import logging
from typing import Optional

from config import AMCConfig
from extractor import AdaptiveMemoryExtractor
from filtering import ArtifactFilter

log = logging.getLogger("ram_weaver.amc.pipeline")


class AdaptiveMemoryCarver:
    """End-to-end Stage 1 orchestrator.

    Args:
        config: AMC configuration object.  Defaults to ``AMCConfig()`` which
                reads settings from environment variables.
    """

    def __init__(self, config: Optional[AMCConfig] = None) -> None:
        self.cfg = config or AMCConfig()
        self.extractor = AdaptiveMemoryExtractor(self.cfg)
        self.artifact_filter = ArtifactFilter(self.cfg)

    def run(
        self,
        dump_path: str,
        pid: int,
        output_name: str = "amc_output.txt",
    ) -> str:
        """Execute the full AMC pipeline.

        Args:
            dump_path:   Path to the system memory image.
            pid:         Target process PID.
            output_name: Filename for the filtered chunk output.

        Returns:
            Absolute path to the chunk file, or empty string on failure.
        """
        log.info("=" * 60)
        log.info("RAM-Weaver Stage 1 – AMC Pipeline started")
        log.info("  Dump : %s", dump_path)
        log.info("  PID  : %d", pid)
        log.info("=" * 60)

        # Stage 1a – Adaptive Memory Extraction
        binary_files = self.extractor.extract(dump_path, pid)
        if not binary_files:
            log.error("No memory regions extracted. Aborting.")
            return ""

        log.info("Extracted %d binary region file(s).", len(binary_files))

        # Stage 1b – Targeted Artifact Filtering
        chunks = self.artifact_filter.filter(binary_files)
        if not chunks:
            log.warning("No chunks survived filtering.")
            return ""

        output_path = self.artifact_filter.save_chunks(chunks, output_name)

        log.info("=" * 60)
        log.info("AMC complete. Output: %s", output_path)
        log.info("Total chunks: %d", len(chunks))
        log.info("=" * 60)
        return output_path
