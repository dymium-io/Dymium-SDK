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

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  KEY_FILE_CANDIDATES=(
    "/home/dennis/Work/openai_api_key.txt"
    "$HOME/Work/openai_api_key.txt"
    "$ROOT_DIR/../openai_api_key.txt"
  )
  for key_file in "${KEY_FILE_CANDIDATES[@]}"; do
    if [[ -f "$key_file" ]]; then
      OPENAI_API_KEY="$(tr -d '\r\n' < "$key_file")"
      export OPENAI_API_KEY
      break
    fi
  done
fi

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "Missing OPENAI_API_KEY and no key file found." >&2
  exit 1
fi

# Default to local Presidio test container
export PRESIDIO_URL="${PRESIDIO_URL:-http://localhost:5000}"

# Install SDK + test deps + LangChain OpenAI integration
"$PYTHON_BIN" -m pip install -e "$ROOT_DIR/python[test]" langchain-openai >/dev/null

# Ensure Presidio is running for tests
docker compose -f "$ROOT_DIR/utilities/docker-compose.presidio.yml" up -d presidio-analyzer >/dev/null

"$PYTHON_BIN" "$ROOT_DIR/tests/run_langchain_agent_demo.py"
