#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    echo "python3 not found" >&2
    exit 1
  fi
fi

export HF_MODEL_ID="${HF_MODEL_ID:-dymium/Dymium-NER-v1}"

"$PYTHON_BIN" -m pip install -e "$ROOT_DIR/python[hf]" >/dev/null

"$PYTHON_BIN" "$ROOT_DIR/tests/run_huggingface_detector_smoke.py"
