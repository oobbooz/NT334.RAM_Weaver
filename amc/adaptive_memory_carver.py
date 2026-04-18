"""
RAM-Weaver: Adaptive Memory Carver (AMC)
=========================================
Stage 1 của pipeline RAM-Weaver.
Thực hiện 2 bước:
  1. Adaptive Memory Extraction (AME) — chọn Heap Mode hoặc PrivateMemory Mode
  2. Targeted Artifact Filtering     — Regex filter + JSON-like pattern filter

Yêu cầu:
  - Volatility3 đã cài và có trong PATH (hoặc chỉ định đường dẫn)
  - File memory dump (.raw / .mem / .vmem)
  - PID của tiến trình LINE Messenger trên Windows guest
"""

import os
import re
import json
import subprocess
import struct
import sys
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("AMC")


# ---------------------------------------------------------------------------
# Cấu hình
# ---------------------------------------------------------------------------

@dataclass
class AMCConfig:
    """Tất cả tham số có thể tuỳ chỉnh của AMC."""

    # Đường dẫn tới Volatility3 (vol.py hoặc vol3 binary)
    volatility_path: str = "/home/kali/volatility3/vol.py"

    # Thư mục lưu raw VAD dump từ Volatility
    vad_dump_dir: str = "./dumps/vad_raw"

    # Thư mục lưu text chunks sau khi filter
    output_dir: str = "./output/amc_chunks"

    # Encoding cần tìm trong memory
    encodings: list = field(default_factory=lambda: ["utf-8", "utf-16-le"])

    # Độ dài chuỗi tối thiểu để giữ lại (tránh garbage ngắn)
    min_string_len: int = 8

    # Chế độ extraction: "auto", "heap", "private_memory"
    extraction_mode: str = "auto"

    # Các từ khoá JSON cần tìm (theo LINE Messenger)
    json_keys_of_interest: list = field(default_factory=lambda: [
        "text", "from", "to", "createdTime", "chatId",
        "contentType", "status", "type", "id"
    ])

    # Ngưỡng tỉ lệ JSON key match để giữ lại một block
    json_key_threshold: int = 2  # Cần ít nhất N key trong 1 block

    # Patterns noise cần loại bỏ (regex)
    noise_patterns: list = field(default_factory=lambda: [
        r"[A-Za-z]:\\[\w\\/. -]+",              # Windows file paths
        r"\{[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\}",  # GUIDs
        r"https?://[^\s\"'<>]{5,200}",           # URLs (giữ lại nếu trong JSON)
        r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}",       # IP addresses
        r"[A-Za-z0-9+/]{40,}={0,2}",            # Base64 dài (binary noise)
        r"\\x[0-9a-fA-F]{2}",                   # Hex escape sequences
        r"\x00+",                                # Null bytes
    ])


# ---------------------------------------------------------------------------
# Bước 1: Adaptive Memory Extraction (AME)
# ---------------------------------------------------------------------------

class AdaptiveMemoryExtractor:
    """
    Chọn và thực thi chiến lược extraction phù hợp với ứng dụng target.

    Hai chế độ (theo paper):
    - Heap Mode       : Parse PEB -> dump các VAD vùng heap
    - PrivateMemory   : Duyệt VAD tree -> dump tất cả PrivateMemory,
                        loại trừ VadImageMap (DLL/EXE) và device memory
    """

    def __init__(self, config: AMCConfig):
        self.cfg = config
        os.makedirs(config.vad_dump_dir, exist_ok=True)

    def extract(self, dump_path: str, pid: int) -> list[str]:
        """
        Entry point: tự động chọn mode hoặc dùng mode được chỉ định.
        Trả về danh sách đường dẫn tới các raw binary chunk đã dump.
        """
        if not Path(dump_path).is_file():
            log.error(f"Không tìm thấy dump file: {dump_path}")
            return []

        mode = self._detect_mode(dump_path, pid)
        log.info(f"Extraction mode được chọn: {mode.upper()}")

        if mode == "heap":
            files = self._heap_mode(dump_path, pid)
        else:
            files = self._private_memory_mode(dump_path, pid)

        if files:
            return files

        # Process dump (.dmp) thường không dựng được kernel layer cho VadInfo.
        # Fallback: dùng trực tiếp dump file cho bước string extraction/filtering.
        if dump_path.lower().endswith(".dmp"):
            log.warning(
                "Volatility không trích xuất được VAD từ process dump. "
                "Fallback: dùng trực tiếp file .dmp cho bước string extraction/filtering."
            )
            return [dump_path]

        return []

    def _detect_mode(self, dump_path: str, pid: int) -> str:
        """
        Heuristic tự động chọn mode:
        - Nếu app có heap Windows chuẩn (PEB trỏ tới ProcessHeap) -> Heap Mode
        - Nếu app dùng custom allocator như LINE -> PrivateMemory Mode

        LINE Messenger thường phù hợp PrivateMemory Mode.
        """
        if self.cfg.extraction_mode != "auto":
            return self.cfg.extraction_mode

        # Mặc định chọn PrivateMemory
        log.info("Auto-detect: default về PrivateMemory mode (phù hợp LINE Messenger)")
        return "private_memory"

    def _heap_mode(self, dump_path: str, pid: int) -> list[str]:
        """
        Heap Mode:
        - Dùng Volatility windows.vadinfo để tìm vùng heap
        - Lọc các VAD region có tag Heap
        - Dump từng region bằng windows.vadump
        """
        log.info("Đang lấy danh sách VAD regions (Heap Mode)...")

        # Lấy thông tin VAD
        vad_info = self._run_volatility(
            dump_path,
            ["windows.vadinfo.VadInfo", "--pid", str(pid)]
        )

        heap_regions = []
        for line in vad_info.splitlines():
            # Lọc các region có VadS (thường là heap) và không phải image
            if "VadS" in line and "READWRITE" in line:
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        start = int(parts[0], 16)
                        end = int(parts[1], 16)
                        heap_regions.append((start, end))
                    except ValueError:
                        continue

        log.info(f"Tìm thấy {len(heap_regions)} heap regions")
        return self._dump_regions(dump_path, pid, heap_regions, prefix="heap")

    def _private_memory_mode(self, dump_path: str, pid: int) -> list[str]:
        """
        PrivateMemory Mode (dùng cho LINE Messenger):
        - Duyệt toàn bộ VAD tree
        - Giữ lại các vùng PrivateMemory (Private = True)
        - Loại bỏ VadImageMap (DLL/EXE mapped files)
        - Loại bỏ device-mapped memory
        """
        log.info("Đang lấy danh sách VAD regions (PrivateMemory Mode)...")

        vad_info = self._run_volatility(
            dump_path,
            ["windows.vadinfo.VadInfo", "--pid", str(pid)]
        )

        private_regions = []
        for line in vad_info.splitlines():
            # Bỏ qua header và dòng trống
            if not line.strip() or line.startswith("Volatility") or line.startswith("PID"):
                continue

            parts = line.split()
            if len(parts) < 5:
                continue

            try:
                # Cột Volatility 3: 0:PID, 1:Process, 2:Offset, 3:Start, 4:End, 5:Tag, 6:Protection, 10:Type
                start = int(parts[3], 16)
                end = int(parts[4], 16)
                vad_type = parts[10] if len(parts) > 10 else ""
                protection = parts[6] if len(parts) > 6 else ""

                # Loại trừ VadImageMap (DLL/EXE)
                if "Mapped" in vad_type or "Image" in vad_type:
                    continue
                # Loại trừ device memory (thường không có READWRITE)
                if "READONLY" in protection and "EXECUTE" not in protection:
                    continue
                # Chỉ giữ vùng có thể đọc/ghi (PrivateMemory)
                if "READWRITE" in protection or "WRITECOPY" in protection:
                    private_regions.append((start, end))

            except (ValueError, IndexError):
                continue

        log.info(f"Tìm thấy {len(private_regions)} PrivateMemory regions")
        return self._dump_regions(dump_path, pid, private_regions, prefix="private")

    def _dump_regions(
        self,
        dump_path: str,
        pid: int,
        regions: list[tuple],
        prefix: str
    ) -> list[str]:
        """
        Dump từng VAD region ra file binary bằng Volatility vadump.
        Trả về danh sách đường dẫn file đã dump.
        """
        output_files = []

        # Dùng windows.vadump để dump toàn bộ VAD của process
        dump_subdir = os.path.join(self.cfg.vad_dump_dir, f"pid_{pid}")
        os.makedirs(dump_subdir, exist_ok=True)

        log.info(f"Đang dump VAD regions vào {dump_subdir}...")
        self._run_volatility(
            dump_path,
            ["-o", dump_subdir, "windows.vadinfo.VadInfo", "--pid", str(pid), "--dump"]
        )

        dumped = list(Path(dump_subdir).glob("*.dmp"))
        log.info(f"Đã dump {len(dumped)} file VAD")

        # Lọc chỉ giữ các region thuộc danh sách cần thiết
        for f in dumped:
            fname = f.name
            match = re.search(r"vad\.(?:0x)?([0-9a-fA-F]+)-(?:0x)?([0-9a-fA-F]+)\.dmp", fname)
            if match:
                file_start = int(match.group(1), 16)
                file_end = int(match.group(2), 16)
                # Kiểm tra xem region này có trong danh sách cần giữ không
                for (reg_start, reg_end) in regions:
                    if abs(file_start - reg_start) < 0x1000:  # tolerance 4KB
                        output_files.append(str(f))
                        break
            else:
                # Nếu không parse được tên thì giữ lại
                output_files.append(str(f))

        log.info(f"Số region được giữ lại sau filter: {len(output_files)}")
        return output_files

    def _run_volatility(self, dump_path: str, args: list) -> str:
        """Chạy lệnh Volatility3 và trả về stdout."""
        # Ưu tiên Python từ env, nếu không có thì dùng interpreter hiện tại
        python_exe = os.environ.get("RAM_WEAVER_PYTHON") or sys.executable
        vol_script = self.cfg.volatility_path 

        # Nếu vol_script là launcher (.exe) thì chạy trực tiếp
        if str(vol_script).lower().endswith(".exe"):
            cmd = [vol_script, "-f", dump_path] + args
        else:
            cmd = [python_exe, vol_script, "-f", dump_path] + args
        
        log.info(f"Đang thực thi: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode != 0:
                log.warning(f"Volatility stderr: {result.stderr[:500]}")
            return result.stdout
        except subprocess.TimeoutExpired:
            log.error("Volatility timeout!")
            return ""
        except FileNotFoundError:
            log.error(f"Không tìm thấy executable/script: {cmd[0]}")
            return ""

# ---------------------------------------------------------------------------
# Bước 2: Targeted Artifact Filtering
# ---------------------------------------------------------------------------

class ArtifactFilter:
    """
    Nhận binary dump files -> extract text -> filter noise -> output text chunks.

    Hai lớp filter (theo paper):
    1. Regex-based Filtering  : loại system noise (paths, GUIDs, binary garbage)
    2. JSON-like Pattern Filter: giữ lại structured data có chứa user activity
    """

    def __init__(self, config: AMCConfig):
        self.cfg = config
        self._compiled_noise = [re.compile(p) for p in config.noise_patterns]
        os.makedirs(config.output_dir, exist_ok=True)

    def filter(self, binary_files: list[str]) -> list[str]:
        """
        Xử lý danh sách binary dump files.
        Trả về danh sách text chunks đã được filter.
        """
        all_chunks = []
        total_raw_size = 0
        total_clean_size = 0

        for fpath in binary_files:
            if not os.path.exists(fpath):
                continue

            file_size = os.path.getsize(fpath)
            total_raw_size += file_size

            # Extract strings từ binary
            raw_strings = self._extract_strings(fpath)

            # Filter 1: Regex noise removal
            after_regex = self._regex_filter(raw_strings)

            # Filter 2: JSON-like pattern filter
            json_chunks = self._json_pattern_filter(after_regex)

            all_chunks.extend(json_chunks)
            total_clean_size += sum(len(c) for c in json_chunks)

        # Tính toán compression stats
        if total_raw_size > 0:
            reduction = (1 - total_clean_size / total_raw_size) * 100
            log.info(f"Raw size  : {total_raw_size / 1024:.2f} KB")
            log.info(f"Clean size: {total_clean_size / 1024:.2f} KB")
            log.info(f"Reduction : {reduction:.1f}%")

        return all_chunks

    def _extract_strings(self, binary_path: str) -> list[str]:
        """
        Extract printable strings từ binary file.
        Hỗ trợ UTF-8 và UTF-16-LE (theo paper).
        """
        strings = []

        with open(binary_path, "rb") as f:
            data = f.read()

        # UTF-8 / ASCII strings
        if "utf-8" in self.cfg.encodings:
            pattern = rb"[\x20-\x7e\x09\x0a\x0d]{" + str(self.cfg.min_string_len).encode() + rb",}"
            matches = re.findall(pattern, data)
            for m in matches:
                try:
                    strings.append(m.decode("utf-8", errors="ignore"))
                except Exception:
                    pass

        # UTF-16-LE strings (Windows wide strings)
        if "utf-16-le" in self.cfg.encodings:
            pattern_16 = rb"(?:[\x20-\x7e]\x00){" + str(self.cfg.min_string_len).encode() + rb",}"
            matches_16 = re.findall(pattern_16, data)
            for m in matches_16:
                try:
                    decoded = m.decode("utf-16-le", errors="ignore")
                    if len(decoded) >= self.cfg.min_string_len:
                        strings.append(decoded)
                except Exception:
                    pass

        return strings

    def _regex_filter(self, strings: list[str]) -> list[str]:
        """
        Filter 1: Loại bỏ system noise bằng regex.
        Áp dụng từng pattern trong noise_patterns.
        """
        cleaned = []
        for s in strings:
            # Áp dụng tất cả noise patterns
            cleaned_s = s
            for pattern in self._compiled_noise:
                cleaned_s = pattern.sub("", cleaned_s)

            # Loại bỏ nếu quá ngắn sau khi clean
            cleaned_s = cleaned_s.strip()
            if len(cleaned_s) >= self.cfg.min_string_len:
                cleaned.append(cleaned_s)

        return cleaned

    def _json_pattern_filter(self, strings: list[str]) -> list[str]:
        """
        Filter 2: JSON-like Pattern Filter.
        Tìm và giữ lại các block có chứa JSON key-value patterns liên quan
        tới user activity (chat messages, timestamps, user IDs...).

        Quan trọng: giữ lại TOÀN BỘ context xung quanh JSON block để
        LLM có thể hiểu cấu trúc (theo paper).
        """
        valuable_chunks = []
        keys_pattern = "|".join(
            re.escape(f'"{k}"') for k in self.cfg.json_keys_of_interest
        )
        keys_regex = re.compile(keys_pattern)

        for s in strings:
            # Đếm số JSON key của interest xuất hiện trong string
            matches = keys_regex.findall(s)
            if len(matches) >= self.cfg.json_key_threshold:
                # String này có đủ JSON keys -> giữ lại
                valuable_chunks.append(s)
                continue

            # Thử parse JSON (partial)
            # LINE lưu messages dưới dạng JSON objects trong memory
            json_blocks = self._extract_json_blocks(s)
            for block in json_blocks:
                block_matches = keys_regex.findall(json.dumps(block))
                if len(block_matches) >= self.cfg.json_key_threshold:
                    valuable_chunks.append(json.dumps(block, ensure_ascii=False))

        # Deduplication (giữ thứ tự, bỏ duplicate)
        seen = set()
        deduped = []
        for chunk in valuable_chunks:
            key = chunk[:100]  # Dùng 100 ký tự đầu làm key
            if key not in seen:
                seen.add(key)
                deduped.append(chunk)

        log.info(f"JSON-like filter: giữ lại {len(deduped)} chunks")
        return deduped

    def _extract_json_blocks(self, text: str) -> list[dict]:
        """
        Tìm và parse tất cả JSON objects trong một chuỗi text.
        Hỗ trợ JSON không hoàn chỉnh (fragmented).
        """
        blocks = []
        # Tìm các đoạn bắt đầu bằng { và kết thúc bằng }
        brace_pattern = re.compile(r'\{[^{}]*\}')
        for match in brace_pattern.finditer(text):
            try:
                obj = json.loads(match.group())
                if isinstance(obj, dict) and len(obj) > 0:
                    blocks.append(obj)
            except json.JSONDecodeError:
                pass
        return blocks

    def save_chunks(self, chunks: list[str], output_file: str) -> str:
        """Lưu các chunks ra file text để truyền vào LLM."""
        output_path = os.path.join(self.cfg.output_dir, output_file)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n---CHUNK---\n".join(chunks))
        size_kb = os.path.getsize(output_path) / 1024
        log.info(f"Đã lưu {len(chunks)} chunks: {output_path} ({size_kb:.2f} KB)")
        return output_path


# ---------------------------------------------------------------------------
# Entry point: Full AMC Pipeline
# ---------------------------------------------------------------------------

class AdaptiveMemoryCarver:
    """
    Orchestrate toàn bộ Stage 1:
    AME (extraction) -> ArtifactFilter (filtering) -> output chunks
    """

    def __init__(self, config: Optional[AMCConfig] = None):
        self.cfg = config or AMCConfig()
        self.extractor = AdaptiveMemoryExtractor(self.cfg)
        self.filter = ArtifactFilter(self.cfg)

    def run(self, dump_path: str, pid: int, output_name: str = "amc_output.txt") -> str:
        """
        Chạy toàn bộ AMC pipeline.

        Args:
            dump_path  : Đường dẫn tới memory dump file
            pid        : PID của LINE Messenger process
            output_name: Tên file output

        Returns:
            Đường dẫn tới file text chunks (input cho Stage 2 - LLM)
        """
        log.info("=" * 60)
        log.info("RAM-Weaver AMC Pipeline bắt đầu")
        log.info(f"  Dump   : {dump_path}")
        log.info(f"  PID    : {pid}")
        log.info("=" * 60)

        # Bước 1: Extract memory regions
        binary_files = self.extractor.extract(dump_path, pid)
        if not binary_files:
            log.error("Không extract được vùng nhớ nào!")
            return ""

        # Bước 2: Filter artifacts
        chunks = self.filter.filter(binary_files)
        if not chunks:
            log.warning("Không tìm thấy chunk nào sau khi filter!")
            return ""

        # Lưu kết quả
        output_path = self.filter.save_chunks(chunks, output_name)

        log.info("=" * 60)
        log.info(f"AMC hoàn thành. Output: {output_path}")
        log.info(f"Tổng số chunks: {len(chunks)}")
        log.info("=" * 60)

        return output_path


# ---------------------------------------------------------------------------
# Chạy thử (standalone test)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python adaptive_memory_carver.py <dump_path> <pid>")
        print("Example: python adaptive_memory_carver.py memory.raw 1234")
        sys.exit(1)

    dump_path = sys.argv[1]
    pid = int(sys.argv[2])

    config = AMCConfig(
        volatility_path="/home/kali/volatility3/vol.py",       # Thay bằng đường dẫn Volatility3 thực tế
        vad_dump_dir="./dumps/vad",
        output_dir="./output/amc",
        extraction_mode="auto",      # "auto", "heap", "private_memory"
    )

    amc = AdaptiveMemoryCarver(config)
    result = amc.run(dump_path, pid)

    if result:
        print(f"\nOutput luu tai: {result}")
        print(f"  Kích thước: {os.path.getsize(result) / 1024:.2f} KB")
    else:
        print("\nAMC that bai")
