#!/usr/bin/env bash
# =============================================================================
# run_pipeline.sh – RAM-Weaver pipeline runner (updated for modular layout)
# =============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Load .env ────────────────────────────────────────────────────────────────
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  source "$ROOT_DIR/.env"
  set +a
fi

# ── Runtime config ───────────────────────────────────────────────────────────
PYTHON_BIN="${PYTHON_BIN:-python}"

DUMP_PATH="${1:-${RAM_WEAVER_DUMP_PATH:-}}"
PID="${2:-${RAM_WEAVER_PID:-}}"
MODE="${3:-${RAM_WEAVER_MODE:-restore}}"
QUERY_TEXT=""

AMC_OUTPUT_DIR="${RAM_WEAVER_OUTPUT_DIR:-$ROOT_DIR/output}"
CHUNKS_FILE="$AMC_OUTPUT_DIR/amc_output.txt"

# ── Usage ─────────────────────────────────────────────────────────────────────
usage() {
  cat <<USAGE
Usage:
  ./run_pipeline.sh [dump_path] [pid] [restore|query|interactive] ["query text"]
USAGE
}

# ── Validate inputs ─────────────────────────────────────────────────────────
if [[ -z "$DUMP_PATH" || -z "$PID" ]]; then
  echo "[ERROR] Missing dump_path or pid" >&2
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
      QUERY_TEXT="List all messages in chronological order"
    fi
    ;;
  *)
    echo "[ERROR] Invalid mode: $MODE" >&2
    usage
    exit 1
    ;;
esac

# ── Validate Python ──────────────────────────────────────────────────────────
command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
  echo "[ERROR] Python not found: $PYTHON_BIN" >&2
  exit 1
}

# ── FIXED: correct paths theo tree hiện tại ──────────────────────────────────
AMC_RUNNER="$ROOT_DIR/amc/amc_runner.py"
LLM_RUNNER="$ROOT_DIR/llm/llm_runner.py"

if [[ ! -f "$AMC_RUNNER" ]]; then
  echo "[ERROR] Missing: $AMC_RUNNER" >&2
  exit 1
fi

if [[ ! -f "$LLM_RUNNER" ]]; then
  echo "[ERROR] Missing: $LLM_RUNNER" >&2
  exit 1
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo "============================================================"
echo " RAM-Weaver Pipeline"
echo "============================================================"
echo " Dump   : $DUMP_PATH"
echo " PID    : $PID"
echo " Mode   : $MODE"
echo " Output : $AMC_OUTPUT_DIR"
echo "============================================================"

# ── Stage 1: AMC ─────────────────────────────────────────────────────────────
echo "[1/2] AMC stage..."
mkdir -p "$AMC_OUTPUT_DIR"
rm -f "$CHUNKS_FILE"

PYTHONPATH="$ROOT_DIR" "$PYTHON_BIN" "$AMC_RUNNER" "$DUMP_PATH" "$PID"

if [[ ! -f "$CHUNKS_FILE" ]]; then
  echo "[ERROR] AMC output not found: $CHUNKS_FILE" >&2
  exit 1
fi

echo "[OK] AMC done"

# ── Stage 2: LLM ─────────────────────────────────────────────────────────────
echo "[2/2] LLM stage ($MODE)..."

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
echo " DONE"
echo "============================================================"
