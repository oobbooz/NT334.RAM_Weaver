"""Targeted Artifact Filtering – Stage 1b of the RAM-Weaver pipeline.

Implements the two-layer filtering described in paper Section 2.1:

1. **String Extraction** – scans raw binary data for printable ASCII/UTF-8
   and UTF-16-LE character runs of at least ``min_string_len`` characters,
   mirroring the behaviour of the UNIX ``strings`` utility but for both
   encodings simultaneously.

2. **Regex-based Filtering** – applies noise-reduction patterns that remove
   system-generated artefacts (file paths, GUIDs, URLs, IP addresses,
   base64 blobs, hex escapes, null-byte runs) that pollute memory dumps.

3. **JSON-like Pattern Filtering** – identifies strings containing at least
   ``json_key_threshold`` JSON keys from a domain-specific allow-list (e.g.
   ``"text"``, ``"from"``, ``"createdTime"`` for LINE Messenger).  Both
   flat-string matching and nested JSON block parsing are used so that the
   surrounding structural context is preserved for the LLM.

The paper reports that full AMC filtering reduces data size by >99.9% and
improves SNR by ~37 dB compared to naïve ``strings`` output.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Sequence

from config import AMCConfig

log = logging.getLogger("ram_weaver.amc.filtering")

# Compiled pattern for extracting flat JSON objects from a string.
_FLAT_JSON_RE = re.compile(r"\{[^{}]*\}")


class ArtifactFilter:
    """Two-layer filter that extracts high-signal text chunks from binary data."""

    def __init__(self, config: AMCConfig) -> None:
        self.cfg = config
        self._noise_patterns: list[re.Pattern[str]] = [
            re.compile(p) for p in config.noise_patterns
        ]
        self._keys_regex: re.Pattern[str] = re.compile(
            "|".join(
                re.escape(f'"{k}"') for k in config.json_keys_of_interest
            )
        )
        os.makedirs(config.output_dir, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def filter(self, binary_files: Sequence[str]) -> list[str]:
        """Process a list of binary files and return filtered text chunks.

        Args:
            binary_files: Paths to raw VAD dump files produced by Stage 1a.

        Returns:
            Deduplicated list of high-signal text chunks ready for the LLM.
        """
        all_chunks: list[str] = []
        total_raw_bytes = 0
        total_clean_bytes = 0

        for fpath in binary_files:
            if not os.path.isfile(fpath):
                log.warning("File not found, skipping: %s", fpath)
                continue

            file_size = os.path.getsize(fpath)
            total_raw_bytes += file_size
            log.debug("Processing %s (%.2f KB)", fpath, file_size / 1024)

            raw_strings = self._extract_strings(fpath)
            after_regex = self._regex_filter(raw_strings)
            chunks = self._json_pattern_filter(after_regex)
            all_chunks.extend(chunks)
            total_clean_bytes += sum(len(c) for c in chunks)

        if total_raw_bytes > 0:
            reduction_pct = (1.0 - total_clean_bytes / total_raw_bytes) * 100
            # SNR approximation: 20*log10(signal/noise), where signal=clean, noise=raw
            import math
            snr_db = (
                20 * math.log10(total_clean_bytes / total_raw_bytes)
                if total_clean_bytes > 0
                else float("-inf")
            )
            log.info(
                "Raw: %.2f KB | Clean: %.2f KB | Reduction: %.1f%% | SNR delta: %.2f dB",
                total_raw_bytes / 1024,
                total_clean_bytes / 1024,
                reduction_pct,
                -snr_db,  # positive number = noise removed
            )

        return all_chunks

    def save_chunks(self, chunks: Sequence[str], output_file: str) -> str:
        """Persist filtered chunks to a UTF-8 text file.

        Chunks are separated by the sentinel ``---CHUNK---`` so downstream
        code can split them back without ambiguity.

        Args:
            chunks:      Iterable of text chunks to write.
            output_file: Base filename (will be placed under ``config.output_dir``).

        Returns:
            Absolute path of the written file.
        """
        output_path = os.path.join(self.cfg.output_dir, output_file)
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write("\n---CHUNK---\n".join(chunks))
        size_kb = os.path.getsize(output_path) / 1024
        log.info(
            "Saved %d chunks → %s (%.2f KB)", len(chunks), output_path, size_kb
        )
        return output_path

    # ------------------------------------------------------------------ #
    # Step 1 – String extraction                                           #
    # ------------------------------------------------------------------ #

    def _extract_strings(self, binary_path: str) -> list[str]:
        """Extract printable string runs from a binary file.

        Scans for ASCII/UTF-8 runs and UTF-16-LE runs independently,
        mirroring the dual-encoding strategy described in paper Section 2.1.
        """
        with open(binary_path, "rb") as fh:
            data = fh.read()

        strings: list[str] = []
        min_len = self.cfg.min_string_len

        if "utf-8" in self.cfg.encodings:
            pattern = re.compile(
                rb"[\x20-\x7e\x09\x0a\x0d]{" + str(min_len).encode() + rb",}"
            )
            for m in pattern.findall(data):
                try:
                    strings.append(m.decode("utf-8", errors="ignore"))
                except Exception:  # noqa: BLE001
                    pass

        if "utf-16-le" in self.cfg.encodings:
            pattern16 = re.compile(
                rb"(?:[\x20-\x7e]\x00){" + str(min_len).encode() + rb",}"
            )
            for m in pattern16.findall(data):
                try:
                    decoded = m.decode("utf-16-le", errors="ignore")
                    if len(decoded) >= min_len:
                        strings.append(decoded)
                except Exception:  # noqa: BLE001
                    pass

        return strings

    # ------------------------------------------------------------------ #
    # Step 2 – Regex-based noise filtering                                 #
    # ------------------------------------------------------------------ #

    def _regex_filter(self, strings: list[str]) -> list[str]:
        """Remove system-generated noise patterns from each string.

        Patterns are compiled once in ``__init__`` for efficiency.
        Strings that become too short after cleaning are discarded.
        """
        cleaned: list[str] = []
        for s in strings:
            result = s
            for pattern in self._noise_patterns:
                result = pattern.sub("", result)
            result = result.strip()
            if len(result) >= self.cfg.min_string_len:
                cleaned.append(result)
        return cleaned

    # ------------------------------------------------------------------ #
    # Step 3 – JSON-like pattern filtering                                 #
    # ------------------------------------------------------------------ #

    def _json_pattern_filter(self, strings: list[str]) -> list[str]:
        """Retain only strings that contain domain-relevant JSON keys.

        Two strategies are combined (paper Section 2.1):
        - **Flat scan**: count key occurrences directly in the raw string.
        - **Block parse**: extract well-formed ``{...}`` blocks and check
          each parsed dict for key coverage.

        A deduplication step (keyed on the first 100 characters) prevents
        the same memory artefact from appearing multiple times in the output.
        """
        valuable: list[str] = []
        threshold = self.cfg.json_key_threshold

        for s in strings:
            # Strategy A: flat key scan
            if len(self._keys_regex.findall(s)) >= threshold:
                valuable.append(s)
                continue

            # Strategy B: parse flat JSON blocks from the string
            for block in self._extract_json_blocks(s):
                serialised = json.dumps(block, ensure_ascii=False)
                if len(self._keys_regex.findall(serialised)) >= threshold:
                    valuable.append(serialised)
                    break

        # Deduplicate by first-100-char key
        seen: set[str] = set()
        deduped: list[str] = []
        for chunk in valuable:
            key = chunk[:100]
            if key not in seen:
                seen.add(key)
                deduped.append(chunk)

        log.info("JSON-like filter: retained %d chunks.", len(deduped))
        return deduped

    def _extract_json_blocks(self, text: str) -> list[dict]:
        """Parse all flat ``{...}`` JSON objects found within ``text``."""
        blocks: list[dict] = []
        for match in _FLAT_JSON_RE.finditer(text):
            try:
                obj = json.loads(match.group())
                if isinstance(obj, dict) and obj:
                    blocks.append(obj)
            except json.JSONDecodeError:
                pass
        return blocks
