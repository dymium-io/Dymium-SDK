#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/utilities/docker-compose.presidio.yml"

if command -v docker >/dev/null 2>&1; then
  if docker compose version >/dev/null 2>&1; then
    docker compose -f "$COMPOSE_FILE" up -d
  else
    docker-compose -f "$COMPOSE_FILE" up -d
  fi
else
  echo "Docker is required to run Presidio." >&2
  exit 1
fi

echo "Presidio Analyzer is starting on http://localhost:5000"
