#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "[1/4] Python-Version prüfen"
python3 --version

echo "[2/4] Virtuelle Umgebung erstellen (.venv)"
python3 -m venv .venv

echo "[3/4] Abhängigkeiten installieren"
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "[4/4] Optional empfohlene Systemtools prüfen"
for cmd in wpctl pactl aplay arecord ffmpeg; do
  if command -v "$cmd" >/dev/null 2>&1; then
    echo "  - $cmd: OK"
  else
    echo "  - $cmd: FEHLT (optional, aber empfohlen)"
  fi
done

echo
echo "Installation abgeschlossen."
echo "Starten mit: ./scripts/run.sh"
