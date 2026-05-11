"""Adaptive Memory Extraction (AME) – Stage 1a of the RAM-Weaver pipeline.

Implements two extraction modes described in the paper (Section 2.1):

* **Heap Mode** – parses the Process Environment Block (PEB) to identify
  VAD regions that back heap allocations.  Uses ``windows.vadinfo.VadInfo``
  and filters for ``VadS`` + ``READWRITE`` entries.

* **PrivateMemory Mode** (default for LINE Messenger) – traverses the full
  VAD tree and dumps every region marked as PrivateMemory, intelligently
  skipping mapped executable files (``VadImageMap``) and device-backed
  regions.

Volatility 3 is used for all VAD operations.  The class tries multiple
entrypoints (``vol.py``, the ``vol`` console script, etc.) and falls back
gracefully, including a direct-file fallback for ``.dmp`` process dumps that
lack full kernel metadata.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import AMCConfig

log = logging.getLogger("ram_weaver.amc.extractor")

# Regex to parse VAD dump filenames produced by Volatility 3.
# Example: pid.123.vad.0x00400000-0x004fffff.dmp
_VAD_FNAME_RE = re.compile(
    r"vad\.(?:0x)?([0-9a-fA-F]+)-(?:0x)?([0-9a-fA-F]+)\.dmp",
    re.IGNORECASE,
)

# Maximum address offset (in bytes) for matching a VAD dump file to a region.
_REGION_MATCH_TOLERANCE = 0x1000


class AdaptiveMemoryExtractor:
    """Extracts relevant VAD regions from a memory dump using Volatility 3."""

    def __init__(self, config: AMCConfig) -> None:
        self.cfg = config
        os.makedirs(config.vad_dump_dir, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def extract(self, dump_path: str, pid: int) -> list[str]:
        """Extract process memory regions and return a list of binary file paths.

        Args:
            dump_path: Path to the full-system memory image (e.g., ``.vmem``).
            pid:       Target process PID.

        Returns:
            List of absolute paths to extracted VAD ``.dmp`` files, or an
            empty list on failure.
        """
        if not Path(dump_path).is_file():
            log.error("Memory dump not found: %s", dump_path)
            return []

        mode = self._detect_mode(dump_path, pid)
        log.info("Extraction mode selected: %s", mode.upper())

        if mode == "heap":
            files = self._heap_mode(dump_path, pid)
        else:
            files = self._private_memory_mode(dump_path, pid)

        if files:
            return files

        # Graceful fallback: process dumps (.dmp) lack full kernel metadata
        # required by VadInfo.  Feed the dump directly to the filtering stage.
        if dump_path.lower().endswith(".dmp"):
            log.warning(
                "Volatility could not extract VAD regions from process dump. "
                "Falling back to using the .dmp file directly for string "
                "extraction/filtering."
            )
            return [dump_path]

        log.error("No memory regions could be extracted.")
        return []

    # ------------------------------------------------------------------ #
    # Mode detection                                                       #
    # ------------------------------------------------------------------ #

    def _detect_mode(self, dump_path: str, pid: int) -> str:  # noqa: ARG002
        """Select the extraction mode.

        If ``extraction_mode`` is set to something other than ``"auto"`` the
        value is returned as-is.  Otherwise the heuristic defaults to
        ``private_memory`` which the paper shows works best for LINE Messenger.
        """
        if self.cfg.extraction_mode != "auto":
            return self.cfg.extraction_mode
        log.info(
            "Auto-detect: defaulting to PrivateMemory mode "
            "(recommended for LINE Messenger per paper Section 2.1)."
        )
        return "private_memory"

    # ------------------------------------------------------------------ #
    # Heap Mode                                                            #
    # ------------------------------------------------------------------ #

    def _heap_mode(self, dump_path: str, pid: int) -> list[str]:
        """Dump only VAD regions that back process heaps (VadS + READWRITE)."""
        log.info("Heap Mode: fetching VAD region list …")
        vad_info = self._run_volatility(
            dump_path, ["windows.vadinfo.VadInfo", "--pid", str(pid)]
        )
        heap_regions: list[tuple[int, int]] = []
        for line in vad_info.splitlines():
            if "VadS" in line and "READWRITE" in line:
                # Volatility 3 vadinfo columns (space-separated):
                # PID  Offset  VPN_Start  VPN_End  Tag  CommitCharge  ...
                # VPN_Start and VPN_End are hex addresses (columns 2 and 3,
                # 0-indexed) in most vol3 builds.  We scan all tokens for
                # valid hex addresses rather than relying on fixed offsets.
                parts = line.split()
                hex_addrs = []
                for part in parts:
                    clean = part.strip().lstrip("0x").lstrip("0X")
                    if clean and all(c in "0123456789abcdefABCDEF" for c in clean):
                        try:
                            hex_addrs.append(int(part, 16))
                        except ValueError:
                            continue
                # Need at least two addresses (start, end)
                if len(hex_addrs) >= 2:
                    heap_regions.append((hex_addrs[0], hex_addrs[1]))
        log.info("Heap Mode: found %d heap regions.", len(heap_regions))
        return self._dump_regions(dump_path, pid, heap_regions, prefix="heap")

    # ------------------------------------------------------------------ #
    # PrivateMemory Mode                                                   #
    # ------------------------------------------------------------------ #

    def _private_memory_mode(self, dump_path: str, pid: int) -> list[str]:
        """Dump all PrivateMemory VAD regions, excluding mapped/image regions."""
        log.info("PrivateMemory Mode: fetching VAD region list …")
        vad_info = self._run_volatility(
            dump_path, ["windows.vadinfo.VadInfo", "--pid", str(pid)]
        )
        if not vad_info:
            return []

        # Log vài dòng đầu để xác nhận format Volatility output
        first_lines = [l for l in vad_info.splitlines() if l.strip()][:3]
        for i, l in enumerate(first_lines):
            log.info("VAD output line %d: %s", i, l)

        _HEX_RE = re.compile(r"\b(0x[0-9a-fA-F]+|[0-9a-fA-F]{4,})\b")

        # Protection keywords cần giữ lại (writable private memory)
        _KEEP_PROT = {"PAGE_READWRITE", "PAGE_WRITECOPY",
                      "PAGE_EXECUTE_READWRITE", "PAGE_EXECUTE_WRITECOPY"}
        # VadType keywords cần bỏ qua (mapped/image = không phải private)
        _SKIP_TYPE = {"VadImageMap", "VadAwe", "VadDevicePhysicalMemory"}

        private_regions: list[tuple[int, int]] = []
        for line in vad_info.splitlines():
            line = line.strip()
            if not line:
                continue
            # Bỏ qua header và info lines
            if line.startswith(("Volatility", "PID", "Offset", "Pid", "Progress",
                                 "Stacking", "WARNING", "ERROR")):
                continue
            # Bỏ qua line không có số hex
            hex_tokens = _HEX_RE.findall(line)
            if len(hex_tokens) < 4:
                continue

            try:
                start = int(hex_tokens[2], 16)
                end   = int(hex_tokens[3], 16)
                # Sanity check: end > start, và không quá lớn (địa chỉ hợp lệ)
                if end <= start or start == 0:
                    continue
            except ValueError:
                continue

            line_upper = line.upper()

            # Bỏ qua mapped/image regions (paper Section 2.1)
            if any(t.upper() in line_upper for t in _SKIP_TYPE):
                continue
            # "Mapped" hoặc "Image" trong VadType token
            parts = line.split()
            if any("Mapped" in p or "Image" in p for p in parts):
                continue

            # Giữ lại nếu có PAGE_READWRITE hoặc PAGE_WRITECOPY
            if any(p in line_upper for p in _KEEP_PROT):
                private_regions.append((start, end))
                continue

            # Fallback: nếu không có PAGE_ prefix rõ ràng, check READWRITE/WRITECOPY
            if ("READWRITE" in line_upper or "WRITECOPY" in line_upper):
                private_regions.append((start, end))

        log.info(
            "PrivateMemory Mode: found %d private regions.", len(private_regions)
        )
        if not private_regions:
            log.warning(
                "0 private regions found. Possible causes:\n"
                "  1. PID sai (process không phải LINE hoặc đã tắt)\n"
                "  2. memory.raw dump từ full system nhưng PID không match\n"
                "  3. Volatility không nhận diện được symbol/profile\n"
                "  4. Thử chạy thủ công: vol -f <dump> windows.vadinfo.VadInfo --pid %d",
                pid,
            )
            return []
        return self._dump_regions(
            dump_path, pid, private_regions, prefix="private"
        )

    # ------------------------------------------------------------------ #
    # Region dumping helpers                                               #
    # ------------------------------------------------------------------ #

    def _dump_regions(
        self,
        dump_path: str,
        pid: int,
        regions: list[tuple[int, int]],
        prefix: str,
    ) -> list[str]:
        """Ask Volatility to dump the given VAD regions and filter the results.

        Returns the subset of dumped files whose start address matches one of
        the requested ``regions``.
        """
        dump_subdir = os.path.join(self.cfg.vad_dump_dir, f"pid_{pid}")
        os.makedirs(dump_subdir, exist_ok=True)
        log.info("Dumping VAD regions to: %s", dump_subdir)

        self._run_volatility(
            dump_path,
            [
                "-o", dump_subdir,
                "windows.vadinfo.VadInfo",
                "--pid", str(pid),
                "--dump",
            ],
        )

        dumped = list(Path(dump_subdir).glob("*.dmp"))
        log.info("Volatility produced %d dump files.", len(dumped))

        output_files: list[str] = []
        for f in dumped:
            match = _VAD_FNAME_RE.search(f.name)
            if match:
                file_start = int(match.group(1), 16)
                if any(
                    abs(file_start - reg_start) < _REGION_MATCH_TOLERANCE
                    for reg_start, _ in regions
                ):
                    output_files.append(str(f))
            else:
                # Filename format unrecognised – keep it to be safe.
                output_files.append(str(f))

        log.info(
            "Regions retained after address filtering: %d / %d",
            len(output_files),
            len(dumped),
        )
        return output_files

    # ------------------------------------------------------------------ #
    # Volatility subprocess runner                                         #
    # ------------------------------------------------------------------ #

    def _run_volatility(self, dump_path: str, args: list[str]) -> str:
        """Run Volatility 3 with the given arguments and return stdout.

        Tries multiple entrypoints in order:
        1. ``python vol.py``  (if ``RAM_WEAVER_VOL_PATH`` points to vol.py)
        2. ``vol.exe`` / ``vol``  (pip-installed console script)
        3. ``vol``  from PATH

        Returns empty string if all attempts fail.
        """
        python_exe = self.cfg.python_executable or sys.executable
        commands: list[list[str]] = []

        vol_script = self.cfg.volatility_path
        if vol_script:
            if Path(vol_script).is_file():
                commands.append(
                    [python_exe, vol_script, "-f", dump_path] + args
                )
            else:
                log.warning(
                    "RAM_WEAVER_VOL_PATH is not a valid file: %s. "
                    "Will try installed 'vol' console script.",
                    vol_script,
                )

        # Probe for pip-installed console script alongside the Python binary
        scripts_dir = Path(python_exe).parent
        for candidate in (
            scripts_dir / "vol.exe",
            scripts_dir / "vol",
            Path("vol"),   # relies on PATH
        ):
            if candidate == Path("vol") or candidate.is_file():
                commands.append([str(candidate), "-f", dump_path] + args)

        if not commands:
            log.error(
                "No Volatility entrypoint found. "
                "Set RAM_WEAVER_VOL_PATH or install volatility3 via pip."
            )
            return ""

        for cmd in commands:
            log.info("Running: %s", " ".join(cmd))
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.cfg.volatility_timeout,
                )
            except subprocess.TimeoutExpired:
                log.error("Volatility timed out after %ds.", self.cfg.volatility_timeout)
                continue
            except FileNotFoundError:
                continue  # entrypoint not available, try next

            if result.returncode == 0:
                return result.stdout

            if result.stderr:
                log.warning("Volatility stderr: %.500s", result.stderr)

        log.error("All Volatility entrypoints failed.")
        return ""