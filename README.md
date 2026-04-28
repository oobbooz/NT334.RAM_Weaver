## Kiến trúc

```
[Windows VM]                         [Host phân tích (Windows/Kali/WSL)]
LINE Messenger                               │
     │                                        │
     ▼                                        │
WinPmem/ProcDump ──► memory.raw / line.dmp ─►│
                                              │
                                   [Stage 1: AMC]
                                 AdaptiveMemoryCarver
                                              │
                                              ├─ AdaptiveMemoryExtractor
                                              │    ├─ Heap Mode
                                              │    └─ PrivateMemory Mode
                                              │
                                              └─ ArtifactFilter
                                                   ├─ Regex filter
                                                   └─ JSON-like filter

                                   [Stage 2: LLM]
                                              ├─ Restore
                                              └─ Forensic Query
```

---

## Setup

### 1) Cài dependency (Host phân tích)

```bash
pip install volatility3
```

### 2) Chuẩn bị `.env`

Tạo/chỉnh file `.env`, sau đó điền giá trị thật:

- `PYTHON_BIN`: Python dùng cho script pipeline.
- `RAM_WEAVER_DUMP_PATH`: đường dẫn file dump mặc định.
- `RAM_WEAVER_PID`: PID mặc định của process mục tiêu (nếu dùng process dump).
- `RAM_WEAVER_MODE`: mode mặc định (`restore`, `query`, `interactive`).
- `RAM_WEAVER_VOL_PATH`: đường dẫn `vol.py` của Volatility3.
- `RAM_WEAVER_PYTHON`: Python dùng riêng cho AMC.
- `RAM_WEAVER_VAD_DUMP_DIR`: thư mục dump VAD tạm.
- `RAM_WEAVER_OUTPUT_DIR`: thư mục output AMC.
- `RAM_WEAVER_EXTRACTION_MODE`: `auto`, `heap`, `private_memory`.
- `RAM_WEAVER_GEMINI_MODEL`: ví dụ `gemini-2.5-flash`.
- `GEMINI_API_KEY`: API key Gemini.

`run_pipeline.sh` sẽ tự load `.env` nếu file tồn tại.

---

## Quy trình chạy

### Bước 1: Dump memory trong Windows VM

> Nên mở LINE, đăng nhập, mở đoạn chat cần lấy và thao tác vài tin nhắn trước khi dump để artifact đầy đủ hơn.

Mở CMD/PowerShell **Run as Administrator**:

```cmd
:: Cách 1 - dump toàn bộ RAM
winpmem64.exe memory.raw

:: Cách 2 - dump process LINE
tasklist | findstr LINE
procdump.exe -ma <LINE_PID> line_dump.dmp
```

Copy file dump sang máy phân tích.

### Bước 2: Chạy pipeline tự động

```bash
bash run_pipeline.sh
```

Query mode:

```bash
bash run_pipeline.sh <dump_path> <pid> query "List all messages in chronological order"
```

Interactive mode:

```bash
bash run_pipeline.sh <dump_path> <pid> interactive
```

### Bước 3: Chạy thủ công theo từng stage (tuỳ chọn)

Stage 1:

```bash
python amc/amc_runner.py  <dump_path> <pid>
```

Stage 2:

```bash
python llm/ll_runner.py restore ./output/amc/amc_output.txt
python llm/llm_runner.py interactive ./output/amc/amc_output.txt
```

---

## Cấu trúc thư mục chính

```
NT334.RAM_Weaver/
├── README.md
├── .env
├── diagnose.py # Debug / analysis tool
├── test_filtering.py
├── test_metrics.py
├── test_prompts.py
├── run_pipeline.sh
├── amc/
│  ─ amc_runner.py
│ ├── config.py
│ ├── extractor.py
│ ├── filtering.py
│ └── pipeline.py
├── llm/
│  ├── client.py
│  ├── config.py
│  ├── llm_pipeline.py
│  ├── llm_runner.py
│  ├── metrics.py
│  ├── prompts.py
│  ├── query_engine.py
│  └── restorer.py
├── dumps/
└── output/
```
