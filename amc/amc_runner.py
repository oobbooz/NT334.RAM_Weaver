#!/usr/bin/env python
"""Điểm vào CLI cho Giai đoạn 1 – Adaptive Memory Carver (flat layout).

Tất cả module (pipeline.py, extractor.py, filtering.py, config.py)
nằm cùng thư mục với file này. PYTHONPATH được set bởi run_pipeline.sh.

Cách dùng:
    python amc_runner.py <dump_path> <pid>
    # hoặc đặt RAM_WEAVER_DUMP_PATH / RAM_WEAVER_PID trong .env
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    # Thư mục chứa script này (mặc định là `amc/`) và thư mục gốc project
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent

    # Thêm thư mục gốc project vào sys.path để hỗ trợ imports theo package
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    # Load .env ở project root (không override biến môi trường đã set)
    from config import load_env  # noqa: PLC0415
    load_env(project_root / ".env")

    # Parse arguments
    dump_path = (
        sys.argv[1] if len(sys.argv) > 1
        else os.environ.get("RAM_WEAVER_DUMP_PATH", "")
    )
    pid_raw = (
        sys.argv[2] if len(sys.argv) > 2
        else os.environ.get("RAM_WEAVER_PID", "")
    )

    if not dump_path or not pid_raw:
        print(
            "Cách dùng: python amc_runner.py <dump_path> <pid>\n"
            "Hoặc set RAM_WEAVER_DUMP_PATH và RAM_WEAVER_PID trong .env"
        )
        sys.exit(1)

    try:
        pid = int(pid_raw)
    except ValueError:
        print(f"[ERROR] PID phai la so nguyen, nhan duoc: '{pid_raw}'")
        sys.exit(1)

    # Import sau khi sys.path đã được set
    from config import AMCConfig          # noqa: PLC0415  (flat import)
    # Import package-style để tránh lỗi relative imports bên trong amc/*.py
    from amc.pipeline import AdaptiveMemoryCarver  # noqa: PLC0415

    config = AMCConfig(
        volatility_path=os.environ.get("RAM_WEAVER_VOL_PATH"),
        vad_dump_dir=os.environ.get("RAM_WEAVER_VAD_DUMP_DIR", "./vad_dumps"),
        output_dir=os.environ.get("RAM_WEAVER_OUTPUT_DIR", "./output"),
        extraction_mode=os.environ.get("RAM_WEAVER_EXTRACTION_MODE", "auto"),
    )

    amc = AdaptiveMemoryCarver(config)
    result = amc.run(dump_path, pid)

    if result:
        size_kb = os.path.getsize(result) / 1024
        print(f"\nOutput: {result}")
        print(f"  Kich thuoc: {size_kb:.2f} KB")
    else:
        print("\n[ERROR] AMC that bai. Xem log phia tren.")
        sys.exit(1)


if __name__ == "__main__":
    main()
