import logging
import os
import re
import sys
import subprocess
from pathlib import Path

from .config import AMCConfig

log = logging.getLogger("AMC")


class AdaptiveMemoryExtractor:
    def __init__(self, config: AMCConfig):
        self.cfg = config
        os.makedirs(config.vad_dump_dir, exist_ok=True)

    def extract(self, dump_path: str, pid: int) -> list[str]:
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

        # ProcDump/process dump thường không đủ dữ liệu kernel layer cho VadInfo.
        # Nếu là .dmp thì fallback: dùng trực tiếp file để bước string extraction/filtering.
        if dump_path.lower().endswith(".dmp"):
            log.warning(
                "Volatility không trích xuất được VAD từ process dump. "
                "Fallback: dùng trực tiếp file .dmp cho bước string extraction/filtering."
            )
            return [dump_path]

        return []

    def _detect_mode(self, dump_path: str, pid: int) -> str:
        if self.cfg.extraction_mode != "auto":
            return self.cfg.extraction_mode
        log.info("Auto-detect: default về PrivateMemory mode (phù hợp LINE Messenger)")
        return "private_memory"

    def _heap_mode(self, dump_path: str, pid: int) -> list[str]:
        log.info("Đang lấy danh sách VAD regions (Heap Mode)...")
        vad_info = self._run_volatility(dump_path, ["windows.vadinfo.VadInfo", "--pid", str(pid)])
        heap_regions = []
        for line in vad_info.splitlines():
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
        log.info("Đang lấy danh sách VAD regions (PrivateMemory Mode)...")
        vad_info = self._run_volatility(dump_path, ["windows.vadinfo.VadInfo", "--pid", str(pid)])
        if not vad_info:
            return []
        private_regions = []
        for line in vad_info.splitlines():
            if not line.strip() or line.startswith("Volatility") or line.startswith("PID"):
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            try:
                start = int(parts[3], 16)
                end = int(parts[4], 16)
                vad_type = parts[10] if len(parts) > 10 else ""
                protection = parts[6] if len(parts) > 6 else ""
                if "Mapped" in vad_type or "Image" in vad_type:
                    continue
                if "READONLY" in protection and "EXECUTE" not in protection:
                    continue
                if "READWRITE" in protection or "WRITECOPY" in protection:
                    private_regions.append((start, end))
            except (ValueError, IndexError):
                continue
        log.info(f"Tìm thấy {len(private_regions)} PrivateMemory regions")
        if not private_regions:
            return []
        return self._dump_regions(dump_path, pid, private_regions, prefix="private")

    def _dump_regions(self, dump_path: str, pid: int, regions: list[tuple], prefix: str) -> list[str]:
        output_files = []
        dump_subdir = os.path.join(self.cfg.vad_dump_dir, f"pid_{pid}")
        os.makedirs(dump_subdir, exist_ok=True)
        log.info(f"Đang dump VAD regions vào {dump_subdir}...")
        self._run_volatility(dump_path, ["-o", dump_subdir, "windows.vadinfo.VadInfo", "--pid", str(pid), "--dump"])
        dumped = list(Path(dump_subdir).glob("*.dmp"))
        log.info(f"Đã dump {len(dumped)} file VAD")
        for f in dumped:
            fname = f.name
            match = re.search(r"vad\.(?:0x)?([0-9a-fA-F]+)-(?:0x)?([0-9a-fA-F]+)\.dmp", fname)
            if match:
                file_start = int(match.group(1), 16)
                for reg_start, reg_end in regions:
                    if abs(file_start - reg_start) < 0x1000:
                        output_files.append(str(f))
                        break
            else:
                output_files.append(str(f))
        log.info(f"Số region được giữ lại sau filter: {len(output_files)}")
        return output_files

    def _run_volatility(self, dump_path: str, args: list) -> str:
        python_exe = self.cfg.python_executable or sys.executable
        vol_script = self.cfg.volatility_path
        commands: list[list[str]] = []

        if vol_script:
            if Path(vol_script).is_file():
                commands.append([python_exe, vol_script, "-f", dump_path] + args)
            else:
                log.warning(
                    f"RAM_WEAVER_VOL_PATH không hợp lệ: {vol_script}. "
                    "Sẽ thử chạy volatility3 từ package đã cài."
                )

        # Fallback 1: console script `vol` được tạo khi cài pip volatility3
        py_path = Path(python_exe)
        scripts_dir = py_path.parent
        vol_candidates = [
            scripts_dir / "vol.exe",
            scripts_dir / "vol",
            Path("vol"),
        ]
        for vol_cmd in vol_candidates:
            if vol_cmd == Path("vol") or vol_cmd.is_file():
                commands.append([str(vol_cmd), "-f", dump_path] + args)

        last_stderr = ""
        for cmd in commands:
            log.info(f"Đang thực thi: {' '.join(cmd)}")
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            except subprocess.TimeoutExpired:
                log.error("Volatility timeout!")
                continue
            except FileNotFoundError:
                # Python executable hoặc module entrypoint không khả dụng
                continue

            if result.returncode == 0:
                return result.stdout

            last_stderr = result.stderr[:500]
            if last_stderr:
                log.warning(f"Volatility stderr: {last_stderr}")

        if not commands:
            log.error("Không có lệnh Volatility nào để chạy.")
        elif vol_script and not Path(vol_script).is_file():
            log.error("Không chạy được Volatility. Hãy đặt RAM_WEAVER_VOL_PATH tới vol.py hợp lệ, hoặc đảm bảo có launcher `vol` trong cùng môi trường Python.")
        else:
            log.error("Không chạy được Volatility từ tất cả entrypoint đã thử.")
        return ""
