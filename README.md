## Kiến trúc

```
[Windows 10 VM]                    [Host Machine]
LINE Messenger                         │
     │                                 │
     ▼                                 │
WinPmem/DumpIt ──► memory.raw ────────►│
                                       │
                              [Stage 1: AMC]
                         AdaptiveMemoryCarver
                              │
                              ├─ AdaptiveMemoryExtractor
                              │    ├─ Heap Mode (PEB → VAD heaps)
                              │    └─ PrivateMemory Mode (VAD tree)
                              │
                              └─ ArtifactFilter
                                   ├─ Regex-based Filter (noise removal)
                                   └─ JSON-like Pattern Filter
                                       (giữ: text, from, to, createdTime...)

                              [Stage 2: LLM]
                              │
                              ├─ Task A: Text Restoration (EMR/CER)
                              └─ Task B: Forensic Querying (interactive)
```

---

## Setup

### 1. Cài đặt dependencies (Host Machine)

```bash

# Volatility3
pip install volatility3
# Kiểm tra: vol --help
```

### 2. Chuẩn bị Windows VM

```
VM: Windows 10 x64
- Cài LINE Messenger v9.9.0.3633 
- Tai WinPmem
- Shared folder giữa VM và host để chuyển dump file
```

### 3. Set API Key

```bash
export GEMINI_API_KEY="your-gemini-api-key-here"

# Hoặc tạo file .env:
echo "GEMINI_API_KEY=your-key" > .env
```

---

## Quy trình thực hiện

### Bước 1: Dump Memory từ VM

Trong Windows VM, mở CMD với quyền Administrator:

```cmd
# Lấy PID của LINE
tasklist | findstr LINE

# Dump RAM (chọn 1 trong 2 cách)

# Cách 1: WinPmem (dump toàn bộ physical memory)
winpmem.exe memory.raw

# Cách 2: ProcDump (dump process memory cụ thể - nhanh hơn)
procdump.exe -ma <LINE_PID> line_dump.dmp
```

Sau đó copy `memory.raw` sang host machine.

### Bước 2: Chạy phase1 

```bash
python adaptive_memory_carver.py source/mem_capture.raw <pid>
```
### Bước 3: Chạy phase2

Chế độ 1: Khôi phục toàn văn (Restore Mode)
```
python llm_reconstructor.py restore ./output/amc/amc_output.txt
```
Chế độ 2: Truy vấn điều tra tương tác (Interactive Mode)
```
python llm_reconstructor.py interactive ./output/amc/amc_output.txt
```

---

## Cấu trúc thư mục

```
ram_weaver/
├── README.md
│
├── amc/
│   ├── __init__.py
│   └── adaptive_memory_carver.py    # Stage 1: AMC
│       ├── AdaptiveMemoryExtractor  # Heap Mode / PrivateMemory Mode
│       └── ArtifactFilter           # Regex + JSON-like filter
│
├── llm/
│   ├── __init__.py
│   └── llm_reconstructor.py         # Stage 2: LLM
│       ├── GeminiClient             # Gemini API wrapper
│       ├── TextRestorer             # Task A: Restoration
│       └── ForensicQueryEngine      # Task B: Querying
│
│
├── dumps/                           # Raw VAD dumps (auto-created)
│   └── vad_raw/
│
└── output/                          # Kết quả (auto-created)
    ├── amc/                         # AMC filtered chunks
    ├── restored_messages.txt        # Task A output
    ├── query_history.json           # Task B history
    └── s2_report.txt                # Evaluation report
```
