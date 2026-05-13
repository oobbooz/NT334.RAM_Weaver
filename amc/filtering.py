"""Targeted Artifact Filtering – Stage 1b of the RAM-Weaver pipeline.
Cải tiến: Khử trùng lặp MD5 và Bộ lọc Nhiễu Hệ Thống Chuyên Sâu (Smart Blacklist).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import hashlib
from typing import Sequence
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import AMCConfig

log = logging.getLogger("ram_weaver.amc.filtering")

_FLAT_JSON_RE = re.compile(r"\{[^{}]*\}")
_MIN_EXTRACT_LEN: int = 4
_TEXT_VALUE_MIN_LEN: int = 3
_TEXT_VALUE_RE = re.compile(r'"text"\s*:\s*"([^"]{' + str(_TEXT_VALUE_MIN_LEN) + r',})"')
_ATTACHED_TEXT_RE = re.compile(r'([^"{}]{3,100})\{')

# Danh sách đen: Các chuỗi rác hệ thống đặc thù của Windows / Electron
_SYSTEM_NOISE_TOKENS = [
    ".cpp", 
    ".dll", 
    "onecoreuap", 
    "api-ms-win", 
    "windows.ui", 
    "ext-ms-win", 
    "qeventdispatcher", 
    "telemetryhelper",
    "wrtcomposition"
]


class ArtifactFilter:
    def __init__(self, config: AMCConfig) -> None:
        self.cfg = config
        self._noise_patterns: list[re.Pattern[str]] = [re.compile(p) for p in config.noise_patterns]
        self._keys_regex: re.Pattern[str] = re.compile(
            "|".join(re.escape(f'"{k}"') for k in config.json_keys_of_interest)
        )
        os.makedirs(config.output_dir, exist_ok=True)

    def filter(self, binary_files: Sequence[str]) -> list[str]:
        all_chunks: list[str] = []
        total_raw_bytes = 0
        total_clean_bytes = 0

        valid_files = [f for f in binary_files if os.path.isfile(f)]
        
        # ── Pass 1: Thu thập giá trị "vàng" (Global Rescue Set) ──
        log.info("Pass 1: Building global rescue set...")
        global_known: set[str] = set()
        raw_cache: dict[str, list[str]] = {}

        for fpath in valid_files:
            total_raw_bytes += os.path.getsize(fpath)
            raw_strings = self._extract_strings(fpath)
            raw_cache[fpath] = raw_strings
            global_known |= self._collect_text_values(raw_strings)

        log.info(f"Pass 1 complete: {len(global_known)} text values collected.")

        # ── Pass 2: Lọc và Tinh gọn ──
        log.info("Pass 2: Filtering and Slimming down...")
        seen_hashes = set() # Khử trùng lặp tuyệt đối qua toàn bộ files

        for fpath in valid_files:
            raw_strings = raw_cache.pop(fpath)
            after_regex = self._regex_filter(raw_strings)
            
            # Lấy các chunk tiềm năng từ file này qua màng lọc JSON
            file_chunks = self._json_pattern_filter(after_regex, global_known)
            
            for chunk in file_chunks:
                # [TINH GỌN 1] Khử trùng lặp bằng mã băm MD5
                chunk_hash = hashlib.md5(chunk.encode('utf-8')).hexdigest()
                if chunk_hash in seen_hashes:
                    continue
                
                # [TINH GỌN 2] Bộ lọc nhiễu hệ thống nâng cao (Smart Blacklist)
                chunk_lower = chunk.lower()
                has_system_noise = any(noise in chunk_lower for noise in _SYSTEM_NOISE_TOKENS)
                
                if has_system_noise:
                    # Cứu vãn: Nếu chunk chứa nhiễu nhưng LẠI CHỨA một tin nhắn hợp lệ, ta vẫn giữ
                    is_rescued = False
                    for val in global_known:
                        # Chỉ check các giá trị đủ dài để tránh False Positive
                        if len(val) > 4 and val in chunk:
                            is_rescued = True
                            break
                    if not is_rescued:
                        continue # Bỏ qua chunk này, nó chỉ là rác hệ thống

                seen_hashes.add(chunk_hash)
                all_chunks.append(chunk)
                total_clean_bytes += len(chunk)

        log.info(f"Final reduction: {total_raw_bytes/1024:.1f}KB -> {total_clean_bytes/1024:.1f}KB")
        return all_chunks

    def _collect_text_values(self, strings: list[str]) -> set[str]:
        known: set[str] = set()
        for s in strings:
            for m in _TEXT_VALUE_RE.finditer(s):
                val = m.group(1).strip()
                if len(val) >= _TEXT_VALUE_MIN_LEN:
                    known.add(val)
            for m in _ATTACHED_TEXT_RE.finditer(s):
                val = re.sub(r'^\W+|\W+$', '', m.group(1).strip())
                if len(val) >= _TEXT_VALUE_MIN_LEN:
                    known.add(val)
        return known

    def _extract_strings(self, binary_path: str) -> list[str]:
        with open(binary_path, "rb") as fh:
            data = fh.read()
        strings = []
        extract_len = max(_MIN_EXTRACT_LEN, 1)
        # UTF-8
        pattern = re.compile(rb"[\x20-\x7e\x09\x0a\x0d]{" + str(extract_len).encode() + rb",}")
        for m in pattern.findall(data):
            try: strings.append(m.decode("utf-8", errors="ignore"))
            except: pass
        # UTF-16
        pattern16 = re.compile(rb"(?:[\x20-\x7e]\x00){" + str(extract_len).encode() + rb",}")
        for m in pattern16.findall(data):
            try:
                decoded = m.decode("utf-16-le", errors="ignore")
                if len(decoded) >= extract_len: strings.append(decoded)
            except: pass
        return strings

    def _regex_filter(self, strings: list[str]) -> list[str]:
        cleaned = []
        for s in strings:
            res = s
            for p in self._noise_patterns: res = p.sub("", res)
            res = res.strip()
            has_key = bool(self._keys_regex.search(res))
            if len(res) >= (_MIN_EXTRACT_LEN if has_key else self.cfg.min_string_len):
                cleaned.append(res)
        return cleaned

    def _json_pattern_filter(self, strings: list[str], global_known: set[str]) -> list[str]:
        valuable = []
        threshold = self.cfg.json_key_threshold
        for s in strings:
            # Strategy A: Key count
            if len(self._keys_regex.findall(s)) >= threshold:
                valuable.append(s)
                continue
            
            # Strategy B: JSON Block
            kept_by_b = False
            for block, raw_block_str in self._extract_json_blocks(s):
                if len(self._keys_regex.findall(json.dumps(block))) >= threshold:
                    valuable.append(s)
                    kept_by_b = True; break
                
                # Cứu fragmented text kẹp ngoài JSON
                text_outside = re.sub(r'^\W+|\W+$', '', s.replace(raw_block_str, "").strip())
                if text_outside in global_known:
                    valuable.append(s); kept_by_b = True; break
            
            if kept_by_b: continue
            
            # Strategy C & D
            if _TEXT_VALUE_RE.search(s) or s.strip() in global_known:
                valuable.append(s)
                
        return valuable

    def _extract_json_blocks(self, text: str) -> list[tuple[dict, str]]:
        blocks = []
        for m in _FLAT_JSON_RE.finditer(text):
            try:
                obj = json.loads(m.group())
                if isinstance(obj, dict): blocks.append((obj, m.group()))
            except: pass
        return blocks

    def save_chunks(self, chunks: Sequence[str], output_file: str) -> str:
        output_path = os.path.join(self.cfg.output_dir, output_file)
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write("\n---CHUNK---\n".join(chunks))
        return output_path
