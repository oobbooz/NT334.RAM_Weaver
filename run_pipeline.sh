#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"
QUERY_TEXT=""

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ROOT_DIR/.env"
  set +a
fi

DUMP_PATH="${1:-${RAM_WEAVER_DUMP_PATH:-}}"
PID="${2:-${RAM_WEAVER_PID:-}}"
MODE="${3:-${RAM_WEAVER_MODE:-restore}}"

AMC_WRAPPER="$ROOT_DIR/amc/adaptive_memory_carver_wrapper.py"
LLM_WRAPPER="$ROOT_DIR/llm/llm_reconstructor_wrapper.py"
AMC_OUTPUT_DIR="${RAM_WEAVER_OUTPUT_DIR:-$ROOT_DIR/output/amc_chunks}"
CHUNKS_FILE="$AMC_OUTPUT_DIR/amc_output.txt"

usage() {
  cat <<EOF
Usage:
  ./run_pipeline.sh [dump_path] [pid] [restore|query|interactive] [query_text]

Environment variables:
  PYTHON_BIN               Python executable to use (default: python)
  RAM_WEAVER_DUMP_PATH     Dump path (used when arg #1 is not provided)
  RAM_WEAVER_PID           Target process PID (used when arg #2 is not provided)
  RAM_WEAVER_MODE          Pipeline mode: restore | query | interactive
  RAM_WEAVER_VOL_PATH      Path to volatility.py
  RAM_WEAVER_PYTHON        Python executable for AMC stage
  RAM_WEAVER_VAD_DUMP_DIR  VAD dump output directory
  RAM_WEAVER_OUTPUT_DIR    AMC text output directory
  RAM_WEAVER_GEMINI_MODEL  Gemini model name for LLM stage
EOF
}

if [[ -z "$DUMP_PATH" || -z "$PID" ]]; then
  echo "Missing dump_path or pid (set args or RAM_WEAVER_DUMP_PATH/RAM_WEAVER_PID)." >&2
  usage
  exit 1
fi

if [[ "$MODE" == "query" ]]; then
  if [[ $# -ge 4 ]]; then
    QUERY_TEXT="${*:4}"
  else
    QUERY_TEXT="List all messages in chronological order"
  fi
fi

if [[ ! -f "$AMC_WRAPPER" ]]; then
  echo "AMC wrapper not found: $AMC_WRAPPER" >&2
  exit 1
fi

if [[ ! -f "$LLM_WRAPPER" ]]; then
  echo "LLM wrapper not found: $LLM_WRAPPER" >&2
  exit 1
fi

mkdir -p "$AMC_OUTPUT_DIR"
rm -f "$CHUNKS_FILE"

echo "[1/2] Running AMC stage..."
"$PYTHON_BIN" "$AMC_WRAPPER" "$DUMP_PATH" "$PID"

if [[ ! -f "$CHUNKS_FILE" ]]; then
  echo "AMC output not found: $CHUNKS_FILE" >&2
  exit 1
fi

echo "[2/2] Running LLM stage ($MODE)..."
case "$MODE" in
  restore)
    "$PYTHON_BIN" "$LLM_WRAPPER" restore "$CHUNKS_FILE"
    ;;
  query)
    "$PYTHON_BIN" "$LLM_WRAPPER" query "$CHUNKS_FILE" "$QUERY_TEXT"
    ;;
  interactive)
    "$PYTHON_BIN" "$LLM_WRAPPER" interactive "$CHUNKS_FILE"
    ;;
  *)
    echo "Unknown mode: $MODE" >&2
    usage
    exit 1
    ;;
esac
