"""Quy trình giai đoạn 1: Adaptive Memory Carver (AMC).

Điều phối 2 bước con của AMC:
    1) Trích xuất bộ nhớ thích nghi (AME) qua :class:`AdaptiveMemoryExtractor`.
    2) Lọc nhiễu theo mục tiêu qua :class:`ArtifactFilter`.

Cách dùng::

    from ram_weaver.amc import AMCConfig, AdaptiveMemoryCarver

    amc = AdaptiveMemoryCarver(AMCConfig())
    output_path = amc.run("/path/to/memory.vmem", pid=1234)
"""

from __future__ import annotations

import logging
from typing import Optional
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import AMCConfig
from .extractor import AdaptiveMemoryExtractor
from .filtering import ArtifactFilter

log = logging.getLogger("ram_weaver.amc.pipeline")


class AdaptiveMemoryCarver:
    """Bộ điều phối end-to-end cho giai đoạn 1.

    Tham số:
        config: Cấu hình AMC. Mặc định là ``AMCConfig()`` (đọc từ biến môi trường).
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
        """Chạy toàn bộ pipeline AMC.

        Tham số:
            dump_path: Đường dẫn tới file dump bộ nhớ.
            pid: PID của process mục tiêu.
            output_name: Tên file output chứa các chunk sau khi lọc.

        Trả về:
            Đường dẫn tuyệt đối tới file chunk, hoặc chuỗi rỗng nếu thất bại.
        """
        log.info("=" * 60)
        log.info("RAM-Weaver Stage 1 – AMC Pipeline started")
        log.info("  Dump : %s", dump_path)
        log.info("  PID  : %d", pid)
        log.info("=" * 60)

        # Bước 1a – Trích xuất vùng nhớ (AME)
        binary_files = self.extractor.extract(dump_path, pid)
        if not binary_files:
            log.error("No memory regions extracted. Aborting.")
            return ""

        log.info("Extracted %d binary region file(s).", len(binary_files))

        # Bước 1b – Lọc artifact theo mục tiêu
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
