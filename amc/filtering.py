import json
import logging
import os
import re

from .config import AMCConfig

log = logging.getLogger("AMC")


class ArtifactFilter:
    def __init__(self, config: AMCConfig):
        self.cfg = config
        self._compiled_noise = [re.compile(p) for p in config.noise_patterns]
        os.makedirs(config.output_dir, exist_ok=True)

    def filter(self, binary_files: list[str]) -> list[str]:
        all_chunks = []
        total_raw_size = 0
        total_clean_size = 0
        for fpath in binary_files:
            if not os.path.exists(fpath):
                continue
            file_size = os.path.getsize(fpath)
            total_raw_size += file_size
            raw_strings = self._extract_strings(fpath)
            after_regex = self._regex_filter(raw_strings)
            json_chunks = self._json_pattern_filter(after_regex)
            all_chunks.extend(json_chunks)
            total_clean_size += sum(len(c) for c in json_chunks)
        if total_raw_size > 0:
            reduction = (1 - total_clean_size / total_raw_size) * 100
            log.info(f"Raw size  : {total_raw_size / 1024:.2f} KB")
            log.info(f"Clean size: {total_clean_size / 1024:.2f} KB")
            log.info(f"Reduction : {reduction:.1f}%")
        return all_chunks

    def _extract_strings(self, binary_path: str) -> list[str]:
        strings = []
        with open(binary_path, "rb") as f:
            data = f.read()
        if "utf-8" in self.cfg.encodings:
            pattern = rb"[\x20-\x7e\x09\x0a\x0d]{" + str(self.cfg.min_string_len).encode() + rb",}"
            for m in re.findall(pattern, data):
                try:
                    strings.append(m.decode("utf-8", errors="ignore"))
                except Exception:
                    pass
        if "utf-16-le" in self.cfg.encodings:
            pattern_16 = rb"(?:[\x20-\x7e]\x00){" + str(self.cfg.min_string_len).encode() + rb",}"
            for m in re.findall(pattern_16, data):
                try:
                    decoded = m.decode("utf-16-le", errors="ignore")
                    if len(decoded) >= self.cfg.min_string_len:
                        strings.append(decoded)
                except Exception:
                    pass
        return strings

    def _regex_filter(self, strings: list[str]) -> list[str]:
        cleaned = []
        for s in strings:
            cleaned_s = s
            for pattern in self._compiled_noise:
                cleaned_s = pattern.sub("", cleaned_s)
            cleaned_s = cleaned_s.strip()
            if len(cleaned_s) >= self.cfg.min_string_len:
                cleaned.append(cleaned_s)
        return cleaned

    def _json_pattern_filter(self, strings: list[str]) -> list[str]:
        valuable_chunks = []
        keys_pattern = "|".join(re.escape(f'"{k}"') for k in self.cfg.json_keys_of_interest)
        keys_regex = re.compile(keys_pattern)
        for s in strings:
            matches = keys_regex.findall(s)
            if len(matches) >= self.cfg.json_key_threshold:
                valuable_chunks.append(s)
                continue
            for block in self._extract_json_blocks(s):
                block_matches = keys_regex.findall(json.dumps(block))
                if len(block_matches) >= self.cfg.json_key_threshold:
                    valuable_chunks.append(json.dumps(block, ensure_ascii=False))
        seen = set()
        deduped = []
        for chunk in valuable_chunks:
            key = chunk[:100]
            if key not in seen:
                seen.add(key)
                deduped.append(chunk)
        log.info(f"JSON-like filter: giữ lại {len(deduped)} chunks")
        return deduped

    def _extract_json_blocks(self, text: str) -> list[dict]:
        blocks = []
        for match in re.compile(r'\{[^{}]*\}').finditer(text):
            try:
                obj = json.loads(match.group())
                if isinstance(obj, dict) and len(obj) > 0:
                    blocks.append(obj)
            except json.JSONDecodeError:
                pass
        return blocks

    def save_chunks(self, chunks: list[str], output_file: str) -> str:
        output_path = os.path.join(self.cfg.output_dir, output_file)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n---CHUNK---\n".join(chunks))
        size_kb = os.path.getsize(output_path) / 1024
        log.info(f"Đã lưu {len(chunks)} chunks: {output_path} ({size_kb:.2f} KB)")
        return output_path
