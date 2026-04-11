#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -d ".venv" ]]; then
  echo ".venv wurde nicht gefunden. Bitte zuerst ./scripts/install.sh ausführen."
  exit 1
fi

source .venv/bin/activate
export FLASK_HOST="${FLASK_HOST:-0.0.0.0}"
export FLASK_PORT="${FLASK_PORT:-5071}"
python app.py
