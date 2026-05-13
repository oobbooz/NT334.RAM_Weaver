"""Targeted Artifact Filtering – Stage 1b of the RAM-Weaver pipeline.

Implements the two-layer filtering described in paper Section 2.1:

1. **String Extraction** – scans raw binary data for printable ASCII/UTF-8
   and UTF-16-LE character runs of at least ``min_string_len`` characters.

2. **Regex-based Filtering** – applies noise-reduction patterns that remove
   system-generated artefacts.

3. **JSON-like Pattern Filtering** – identifies strings containing JSON keys.
   Strategy C & D act as rescue mechanisms for highly fragmented user text.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from typing import Sequence
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import AMCConfig

log = logging.getLogger("ram_weaver.amc.filtering")

# Compiled pattern for extracting flat JSON objects from a string.
_FLAT_JSON_RE = re.compile(r"\{[^{}]*\}")

_MIN_EXTRACT_LEN: int = 4
_TEXT_VALUE_MIN_LEN: int = 3

_TEXT_VALUE_RE = re.compile(
    r'"text"\s*:\s*"([^"]{' + str(_TEXT_VALUE_MIN_LEN) + r',})"'
)

# [CẢI THIỆN] Regex bắt các đoạn văn bản nằm dính liền ngay trước một khối JSON 
# (Ví dụ: 'nguoi yeu cu{"e2eeMark":1}' -> bắt 'nguoi yeu cu')
_ATTACHED_TEXT_RE = re.compile(r'([^"{}]{3,100})\{')


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
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def filter(self, binary_files: Sequence[str]) -> list[str]:
        all_chunks: list[str] = []
        total_raw_bytes = 0
        total_clean_bytes = 0

        valid_files = [f for f in binary_files if os.path.isfile(f)]
        for fpath in binary_files:
            if not os.path.isfile(fpath):
                log.warning("File not found, skipping: %s", fpath)

        # ── Pass 1: extract strings + build global known_text_values set ──
        log.info("Pass 1: collecting known text values across %d files …", len(valid_files))
        raw_cache: dict[str, list[str]] = {}
        global_known: set[str] = set()

        for fpath in valid_files:
            total_raw_bytes += os.path.getsize(fpath)
            raw_strings = self._extract_strings(fpath)
            raw_cache[fpath] = raw_strings
            global_known |= self._collect_text_values(raw_strings)

        log.info(
            "Pass 1 complete: %d known text values collected from %d files.",
            len(global_known),
            len(valid_files),
        )

        # ── Pass 2: filter each file using the global rescue set ──────────
        log.info("Pass 2: filtering …")
        for fpath in valid_files:
            log.debug("Processing %s (%.2f KB)", fpath,
                      os.path.getsize(fpath) / 1024)
            raw_strings = raw_cache.pop(fpath)
            after_regex = self._regex_filter(raw_strings)
            chunks = self._json_pattern_filter(after_regex, global_known)
            all_chunks.extend(chunks)
            total_clean_bytes += sum(len(c) for c in chunks)

        if total_raw_bytes > 0:
            reduction_pct = (1.0 - total_clean_bytes / total_raw_bytes) * 100
            snr_db = (
                20 * math.log10(total_clean_bytes / total_raw_bytes)
                if total_clean_bytes > 0
                else float("-inf")
            )
            log.info(
                "Raw: %.2f KB | Clean: %.2f KB | Reduction: %.1f%% | SNR: %.2f dB",
                total_raw_bytes / 1024,
                total_clean_bytes / 1024,
                reduction_pct,
                snr_db,
            )

        return all_chunks

    def save_chunks(self, chunks: Sequence[str], output_file: str) -> str:
        output_path = os.path.join(self.cfg.output_dir, output_file)
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write("\n---CHUNK---\n".join(chunks))
        size_kb = os.path.getsize(output_path) / 1024
        log.info(
            "Saved %d chunks → %s (%.2f KB)", len(chunks), output_path, size_kb
        )
        return output_path

    # ------------------------------------------------------------------ #
    # Step 0 (Strategy D pre-pass) – collect known text values           #
    # ------------------------------------------------------------------ #

    def _collect_text_values(self, strings: list[str]) -> set[str]:
        known: set[str] = set()
        for s in strings:
            # [CẢI THIỆN] 1. Quét mẫu "text":"value" chuẩn
            for m in _TEXT_VALUE_RE.finditer(s):
                val = m.group(1).strip()
                if len(val) >= _TEXT_VALUE_MIN_LEN:
                    known.add(val)
            
            # [CẢI THIỆN] 2. Quét các văn bản dính liền với JSON fragments (để cứu dữ liệu)
            for m in _ATTACHED_TEXT_RE.finditer(s):
                val = m.group(1).strip()
                # Loại bỏ các ký tự nhiễu (dấu câu) ở hai đầu
                val = re.sub(r'^\W+|\W+$', '', val)
                if len(val) >= _TEXT_VALUE_MIN_LEN:
                    known.add(val)

        log.debug(
            "Strategy D pre-pass: collected %d known text values.", len(known)
        )
        return known

    # ------------------------------------------------------------------ #
    # Step 1 – String extraction                                         #
    # ------------------------------------------------------------------ #

    def _extract_strings(self, binary_path: str) -> list[str]:
        with open(binary_path, "rb") as fh:
            data = fh.read()

        strings: list[str] = []
        extract_len = max(_MIN_EXTRACT_LEN, 1)

        if "utf-8" in self.cfg.encodings:
            pattern = re.compile(
                rb"[\x20-\x7e\x09\x0a\x0d]{" + str(extract_len).encode() + rb",}"
            )
            for m in pattern.findall(data):
                try:
                    strings.append(m.decode("utf-8", errors="ignore"))
                except Exception:  # noqa: BLE001
                    pass

        if "utf-16-le" in self.cfg.encodings:
            pattern16 = re.compile(
                rb"(?:[\x20-\x7e]\x00){" + str(extract_len).encode() + rb",}"
            )
            for m in pattern16.findall(data):
                try:
                    decoded = m.decode("utf-16-le", errors="ignore")
                    if len(decoded) >= extract_len:
                        strings.append(decoded)
                except Exception:  # noqa: BLE001
                    pass

        return strings

    # ------------------------------------------------------------------ #
    # Step 2 – Regex-based noise filtering                               #
    # ------------------------------------------------------------------ #

    def _regex_filter(self, strings: list[str]) -> list[str]:
        cleaned: list[str] = []
        for s in strings:
            result = s
            for pattern in self._noise_patterns:
                result = pattern.sub("", result)
            result = result.strip()
            has_key = bool(self._keys_regex.search(result))
            min_len = _MIN_EXTRACT_LEN if has_key else self.cfg.min_string_len
            if len(result) >= min_len:
                cleaned.append(result)
        return cleaned

    # ------------------------------------------------------------------ #
    # Step 3 – JSON-like pattern filtering                               #
    # ------------------------------------------------------------------ #

    def _json_pattern_filter(
        self,
        strings: list[str],
        known_text_values: set[str] | None = None,
    ) -> list[str]:
        valuable: list[str] = []
        threshold = self.cfg.json_key_threshold
        _known = known_text_values or set()

        for s in strings:
            # Strategy A: flat key scan
            if len(self._keys_regex.findall(s)) >= threshold:
                valuable.append(s)
                continue

            # Strategy B: parse flat JSON blocks from the string
            kept_by_b = False
            # [CẢI THIỆN] Trích xuất cả dictionary và chuỗi thô của khối JSON
            for block, raw_block_str in self._extract_json_blocks(s):
                serialised = json.dumps(block, ensure_ascii=False)
                if len(self._keys_regex.findall(serialised)) >= threshold:
                    valuable.append(s)
                    kept_by_b = True
                    break
                
                # [CẢI THIỆN B2] Nếu khối JSON hợp lệ nhưng có văn bản bị kẹp bên ngoài (fragmented message)
                text_outside = s.replace(raw_block_str, "").strip()
                clean_outside = re.sub(r'^\W+|\W+$', '', text_outside)
                if len(clean_outside) >= _TEXT_VALUE_MIN_LEN:
                    valuable.append(s)
                    kept_by_b = True
                    break

            if kept_by_b:
                continue

            # Strategy C: text-key rescue – fragment still has "text":"…"
            m = _TEXT_VALUE_RE.search(s)
            if m:
                log.debug(
                    "Strategy C rescue: text value %r (len=%d)",
                    m.group(1)[:60],
                    len(m.group(1)),
                )
                valuable.append(s)
                continue

            # Strategy D: isolated-value rescue.
            s_stripped = s.strip()
            # [CẢI THIỆN] Cho phép substring match thay vì strict match.
            if s_stripped in _known or any(k in s for k in _known if len(k) >= _TEXT_VALUE_MIN_LEN):
                log.debug(
                    "Strategy D rescue: isolated text value %r", s_stripped[:60]
                )
                valuable.append(s)
                continue

        # Deduplicate by full-content hash.
        seen: set[int] = set()
        deduped: list[str] = []
        for chunk in valuable:
            h = hash(chunk)
            if h not in seen:
                seen.add(h)
                deduped.append(chunk)

        log.info("JSON-like filter: retained %d chunks.", len(deduped))
        return deduped

    # [CẢI THIỆN] Sửa kiểu trả về để có được raw string match
    def _extract_json_blocks(self, text: str) -> list[tuple[dict, str]]:
        """Parse all flat ``{...}`` JSON objects found within ``text``."""
        blocks: list[tuple[dict, str]] = []
        for match in _FLAT_JSON_RE.finditer(text):
            try:
                obj = json.loads(match.group())
                if isinstance(obj, dict) and obj:
                    blocks.append((obj, match.group()))
            except json.JSONDecodeError:
                pass
        return blocks
