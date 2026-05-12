"""Unit tests for amc/filtering.py.

Tests are fully offline – no Volatility or LLM calls needed.
Binary test data is crafted to exercise each filtering layer independently.
"""

import json
import os
import tempfile

import pytest

from config import AMCConfig
from amc.filtering import ArtifactFilter


@pytest.fixture()
def default_filter() -> ArtifactFilter:
    cfg = AMCConfig(output_dir=tempfile.mkdtemp())
    return ArtifactFilter(cfg)


@pytest.fixture()
def binary_file_with_line_message(tmp_path) -> str:
    """Write a binary file containing a LINE-like JSON message fragment."""
    msg = json.dumps({
        "text": "Yes and they smile more when they hit the beat",
        "from": "u61c9pf8f6vyhynods8yaqgahw1y0kxp3",
        "to": "u81f4iippue35w6bf0x9kks2p62thzma8",
        "createdTime": 1751437157191,
        "chatId": "u81f4iippue35w6bf0x9kks2p62thzma8",
        "type": 1,
        "status": 2,
        "id": "568086353582752223",
    })
    p = tmp_path / "region.dmp"
    # Pad with null bytes before and after (realistic memory dump)
    p.write_bytes(b"\x00" * 64 + msg.encode("utf-8") + b"\x00" * 64)
    return str(p)


class TestExtractStrings:
    def test_ascii_extraction(self, default_filter, tmp_path):
        data = b"\x00" * 8 + b"hello world" + b"\x00" * 8
        f = tmp_path / "test.bin"
        f.write_bytes(data)
        strings = default_filter._extract_strings(str(f))
        assert any("hello world" in s for s in strings)

    def test_utf16le_extraction(self, default_filter, tmp_path):
        msg = "test message"
        encoded = msg.encode("utf-16-le")
        f = tmp_path / "test.bin"
        f.write_bytes(b"\x00" * 4 + encoded + b"\x00" * 4)
        strings = default_filter._extract_strings(str(f))
        assert any("test message" in s for s in strings)

    def test_short_strings_excluded(self, default_filter, tmp_path):
        f = tmp_path / "short.bin"
        f.write_bytes(b"hi\x00")  # shorter than min_string_len=8
        strings = default_filter._extract_strings(str(f))
        assert not any(s == "hi" for s in strings)


class TestRegexFilter:
    def test_removes_guid(self, default_filter):
        s = "{12345678-1234-1234-1234-123456789abc} some text here"
        result = default_filter._regex_filter([s])
        assert all("{" not in r for r in result)

    def test_removes_url(self, default_filter):
        s = "click https://example.com/path?q=1 for details about something"
        result = default_filter._regex_filter([s])
        assert all("https://" not in r for r in result)

    def test_removes_windows_path(self, default_filter):
        s = "loaded from C:\\Windows\\System32\\ntdll.dll and more words"
        result = default_filter._regex_filter([s])
        assert all("C:\\" not in r for r in result)

    def test_removes_base64_blob(self, default_filter):
        blob = "A" * 50  # 50 chars = base64 pattern
        s = f"prefix {blob} suffix with enough words"
        result = default_filter._regex_filter([s])
        assert all(blob not in r for r in result)

    def test_short_strings_dropped_after_cleaning(self, default_filter):
        # Entire string is a URL; after removal nothing is left
        s = "https://example.com/very/long/path/that/is/long/enough"
        result = default_filter._regex_filter([s])
        assert result == []


class TestJsonPatternFilter:
    def test_retains_chunk_with_enough_keys(self, default_filter):
        chunk = '{"text": "hello", "from": "userA", "createdTime": 1234}'
        result = default_filter._json_pattern_filter([chunk])
        assert len(result) == 1

    def test_discards_chunk_with_too_few_keys(self, default_filter):
        chunk = '{"unrelated_key": "value", "another_key": 123}'
        result = default_filter._json_pattern_filter([chunk])
        assert result == []

    def test_deduplication(self, default_filter):
        chunk = '{"text": "hello", "from": "userA", "createdTime": 1234}'
        # Same chunk twice → should appear only once
        result = default_filter._json_pattern_filter([chunk, chunk])
        assert len(result) == 1


class TestFullFilterPipeline:
    def test_line_message_survives(self, default_filter, binary_file_with_line_message):
        chunks = default_filter.filter([binary_file_with_line_message])
        assert len(chunks) > 0
        combined = " ".join(chunks)
        assert "text" in combined

    def test_missing_file_skipped(self, default_filter):
        chunks = default_filter.filter(["/nonexistent/path/file.dmp"])
        assert chunks == []


class TestSaveChunks:
    def test_saves_and_returns_path(self, default_filter, tmp_path):
        default_filter.cfg.output_dir = str(tmp_path)
        chunks = ["chunk one", "chunk two"]
        path = default_filter.save_chunks(chunks, "out.txt")
        assert os.path.isfile(path)
        content = open(path).read()
        assert "chunk one" in content
        assert "chunk two" in content
        assert "---CHUNK---" in content
