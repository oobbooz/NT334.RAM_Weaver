
import os
import sys
from pathlib import Path

try:
    from .adaptive_memory_carver import AMCConfig, AdaptiveMemoryCarver
except ImportError:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from adaptive_memory_carver import AMCConfig, AdaptiveMemoryCarver


def main() -> None:
    root_dir = Path(__file__).resolve().parents[1]
    env_file = root_dir / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

    dump_path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("RAM_WEAVER_DUMP_PATH", "")
    pid_raw = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("RAM_WEAVER_PID", "")

    if not dump_path or not pid_raw:
        print("Usage: python adaptive_memory_carver_wrapper.py [dump_path] [pid]")
        print("Or set RAM_WEAVER_DUMP_PATH and RAM_WEAVER_PID in .env")
        sys.exit(1)

    pid = int(pid_raw)

    config = AMCConfig(
        volatility_path=os.environ.get("RAM_WEAVER_VOL_PATH") or AMCConfig().volatility_path,
        vad_dump_dir=os.environ.get("RAM_WEAVER_VAD_DUMP_DIR", "./dumps/vad"),
        output_dir=os.environ.get("RAM_WEAVER_OUTPUT_DIR", "./output/amc"),
        extraction_mode=os.environ.get("RAM_WEAVER_EXTRACTION_MODE", "auto"),
    )

    amc = AdaptiveMemoryCarver(config)
    result = amc.run(dump_path, pid)

    if result:
        print(f"\nOutput lưu tại: {result}")
        print(f"  Kích thước: {os.path.getsize(result) / 1024:.2f} KB")
    else:
        print("\nAMC thất bại")
        sys.exit(1)


if __name__ == "__main__":
    main()
