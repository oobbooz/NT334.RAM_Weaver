## Kiến trúc

```
[Windows VM]                         [Host phân tích (Windows/Kali/WSL)]
LINE Messenger                                │
     │                                        │
     ▼                                        │
WinPmem/ProcDump ──► memory.raw / line.dmp ─► │
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
- `RAM_WEAVER_GEMINI_MODEL`: ví dụ `gemini-2.5-flash` (default).
- `GEMINI_API_KEY`: API key Gemini.

Để chạy Gemma miễn phí qua HuggingFace Inference API (paper Table 2):

- Đăng ký tại <https://huggingface.co> → Settings → Access Tokens → New token
- Set `.env`:

  ```
  RAM_WEAVER_LLM_PROVIDER=huggingface
  HF_API_TOKEN=hf_xxxxxxxxxxxxxxxx
  RAM_WEAVER_LLM_MODEL=google/gemma-3-27b-it
  ```

- Model names theo paper Table 2:
  - `google/gemma-3-27b-it` (EMR=40%)
  - `google/gemma-3-12b-it` (EMR=0%)
  - `google/gemma-3-4b-it`  (EMR=30%)

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
python llm/llm_runner.py restore ./output/amc/amc_output.txt
python llm/llm_runner.py interactive ./output/amc/amc_output.txt
```

---

### Chạy các experiment (S1, S2, S3)

Các script experiment có sẵn để tái hiện các kết quả trong bài báo.

- `experiment_s1.py` — S1: AMC Efficacy (Bảng 1)
  - Cú pháp:

    ```bash
    python experiment_s1.py <dump_path> <pid> <ground_truth>
    ```

  - Ví dụ (ground truth trực tiếp):

    ```bash
    python experiment_s1.py dumps/msg_0001.raw 2528 "This sound track was beautiful! It paints the senery in your mind so well I would recomend it even to people who hate vid. game music! I have played the game Chrono Cross but out of all of the games I have ever played it has the best music! It backs away from crude keyboarding and takes a fresher step with grate guitars and soulful orchestras. It would impress anyone who cares to listen! ^_^"
    ```

  - Hoặc dùng file chứa ground truth (1 message mỗi dòng):

    ```bash
    python experiment_s1.py dumps/memory.raw 8616 --gt-file ground_truth.txt
    ```

  - Tùy chọn:
    - `--skip-llm` : chỉ chạy preprocessing (AMC/AME/etc.) mà không gọi LLM.

- `experiment_s2.py` — S2: Single Message Restoration Accuracy (Bảng 2)
  - Chế độ A (đã có sẵn chunk files):

    ```bash
    python experiment_s2.py --chunks-dir chunks/ --gt-file ground_truth.txt [--limit 10] [--throttle 1.0]
    ```

  - Chế độ B (có dumps, chạy AMC trên từng dump):

    ```bash
    python experiment_s2.py --dumps-dir dumps/ --pid <pid> --gt-file ground_truth.txt [--limit 10] [--throttle 1.0]
    ```

  - Ví dụ:

    ```bash
    python experiment_s2.py --chunks-dir chunks/ --gt-file ground_truth.txt --limit 10
    python experiment_s2.py --dumps-dir dumps/ --pid 2528 --gt-file ground_truth.txt
    ```

- `experiment_s3.py` — S3: Contextual Forensic Querying (Hình 2)
  - Chạy đầy đủ (Stage 1 + Stage 2):

    ```bash
    python experiment_s3.py <dump_path> --pid <PID> --queries s3_queries.json
    ```

  - Chạy với file AMC đã có sẵn (bỏ qua Stage 1):

    ```bash
    python experiment_s3.py <dump_path> --amc-file <chunk_path> --queries s3_queries.json
    ```

  - Chạy không dùng Volatility (đọc file dump trực tiếp):

    ```bash
    python experiment_s3.py <dump_path> --no-volatility --queries s3_queries.json
    ```

  - **Input**: File `s3_queries.json` chứa danh sách các câu hỏi điều tra.
  - **Output**:
    - Các câu trả lời chi tiết: `output/s3_answer_<id>.txt`
    - Kết quả tổng hợp: `output/s3_results.json`

Ghi chú:

- Trước khi chạy các experiment hãy đảm bảo `.env` đã được cấu hình đúng (LLM provider, API keys, đường dẫn Volatility, v.v.).
- Kết quả được lưu vào `output/s1_result.txt` (S1) và `output/s2_result_<model>.txt` (S2).

Outputs:

- Per-query answers saved as `./output/s3_answer_###.txt`.
- Summary saved as `./output/s3_summary.json` with keys `n`, `emr`, `avg_token_f1`, `avg_time_s`.

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
│ ├── extractor.py
│ ├── filtering.py
│ └── pipeline.py
├── llm/
│  ├── client.py
│  ├── llm_pipeline.py
│  ├── llm_runner.py
│  ├── metrics.py
│  ├── prompts.py
│  ├── query_engine.py
│  └── restorer.py
├── dumps/
└── output/
```
