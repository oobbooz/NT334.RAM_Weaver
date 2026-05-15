#!/usr/bin/env bash
# =============================================================================
# run_pipeline.sh – Trình chạy pipeline RAM-Weaver (layout module hoá)
# =============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Nạp .env ────────────────────────────────────────────────────────────────
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  source "$ROOT_DIR/.env"
  set +a
fi

# ── Cấu hình chạy ───────────────────────────────────────────────────────────
# Ưu tiên dùng RAM_WEAVER_PYTHON (đồng bộ với code Python). PYTHON_BIN giữ để tương thích.
PYTHON_BIN="${PYTHON_BIN:-${RAM_WEAVER_PYTHON:-python}}"

DUMP_PATH="${1:-${RAM_WEAVER_DUMP_PATH:-}}"
PID="${2:-${RAM_WEAVER_PID:-}}"
MODE="${3:-${RAM_WEAVER_MODE:-restore}}"
QUERY_TEXT=""

AMC_OUTPUT_DIR="${RAM_WEAVER_OUTPUT_DIR:-$ROOT_DIR/output}"
CHUNKS_FILE="$AMC_OUTPUT_DIR/amc_output.txt"

# ── Cách dùng ───────────────────────────────────────────────────────────────
usage() {
  cat <<USAGE
Cách dùng:
  ./run_pipeline.sh [dump_path] [pid] [restore|query|interactive] ["query text"]
USAGE
}

# ── Kiểm tra input ─────────────────────────────────────────────────────────
if [[ -z "$DUMP_PATH" || -z "$PID" ]]; then
  echo "[ERROR] Thiếu dump_path hoặc pid" >&2
  usage
  exit 1
fi

case "$MODE" in
  restore|interactive) ;;
  query)
    if [[ $# -ge 4 ]]; then
      QUERY_TEXT="${*:4}"
    elif [[ -n "${RAM_WEAVER_QUERY:-}" ]]; then
      QUERY_TEXT="$RAM_WEAVER_QUERY"
    else
      QUERY_TEXT="Liệt kê tất cả tin nhắn theo thứ tự thời gian"
    fi
    ;;
  *)
    echo "[ERROR] Mode không hợp lệ: $MODE" >&2
    usage
    exit 1
    ;;
esac

# ── Kiểm tra Python ─────────────────────────────────────────────────────────
command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
  echo "[ERROR] Không tìm thấy Python: $PYTHON_BIN" >&2
  exit 1
}

# ── Đường dẫn theo cây thư mục hiện tại ─────────────────────────────────────
AMC_RUNNER="$ROOT_DIR/amc/amc_runner.py"
LLM_RUNNER="$ROOT_DIR/llm/llm_runner.py"

if [[ ! -f "$AMC_RUNNER" ]]; then
  echo "[ERROR] Thiếu file: $AMC_RUNNER" >&2
  exit 1
fi

if [[ ! -f "$LLM_RUNNER" ]]; then
  echo "[ERROR] Thiếu file: $LLM_RUNNER" >&2
  exit 1
fi

# ── Tóm tắt ─────────────────────────────────────────────────────────────────
echo "============================================================"
echo " RAM-Weaver – Pipeline"
echo "============================================================"
echo " Dump   : $DUMP_PATH"
echo " PID    : $PID"
echo " Mode   : $MODE"
echo " Output : $AMC_OUTPUT_DIR"
echo "============================================================"

# ── Giai đoạn 1: AMC ───────────────────────────────────────────────────────
echo "[1/2] Chạy AMC..."
mkdir -p "$AMC_OUTPUT_DIR"
rm -f "$CHUNKS_FILE"

PYTHONPATH="$ROOT_DIR" "$PYTHON_BIN" "$AMC_RUNNER" "$DUMP_PATH" "$PID"

if [[ ! -f "$CHUNKS_FILE" ]]; then
  echo "[ERROR] Không thấy file output AMC: $CHUNKS_FILE" >&2
  exit 1
fi

echo "[OK] AMC xong"

# ── Giai đoạn 2: LLM ───────────────────────────────────────────────────────
echo "[2/2] Chạy LLM ($MODE)..."

case "$MODE" in
  restore)
    PYTHONPATH="$ROOT_DIR" "$PYTHON_BIN" "$LLM_RUNNER" restore "$CHUNKS_FILE"
    ;;
  query)
    PYTHONPATH="$ROOT_DIR" "$PYTHON_BIN" "$LLM_RUNNER" query "$CHUNKS_FILE" "$QUERY_TEXT"
    ;;
  interactive)
    PYTHONPATH="$ROOT_DIR" "$PYTHON_BIN" "$LLM_RUNNER" interactive "$CHUNKS_FILE"
    ;;
esac

echo "============================================================"
echo " XONG"
echo "============================================================"
