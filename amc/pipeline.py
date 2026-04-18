import logging
from typing import Optional

from .config import AMCConfig
from .extractor import AdaptiveMemoryExtractor
from .filtering import ArtifactFilter

log = logging.getLogger("AMC")


class AdaptiveMemoryCarver:
    def __init__(self, config: Optional[AMCConfig] = None):
        self.cfg = config or AMCConfig()
        self.extractor = AdaptiveMemoryExtractor(self.cfg)
        self.filter = ArtifactFilter(self.cfg)

    def run(self, dump_path: str, pid: int, output_name: str = "amc_output.txt") -> str:
        log.info("=" * 60)
        log.info("RAM-Weaver AMC Pipeline bắt đầu")
        log.info(f"  Dump   : {dump_path}")
        log.info(f"  PID    : {pid}")
        log.info("=" * 60)
        binary_files = self.extractor.extract(dump_path, pid)
        if not binary_files:
            log.error("Không extract được vùng nhớ nào!")
            return ""
        chunks = self.filter.filter(binary_files)
        if not chunks:
            log.warning("Không tìm thấy chunk nào sau khi filter!")
            return ""
        output_path = self.filter.save_chunks(chunks, output_name)
        log.info("=" * 60)
        log.info(f"AMC hoàn thành. Output: {output_path}")
        log.info(f"Tổng số chunks: {len(chunks)}")
        log.info("=" * 60)
        return output_path
