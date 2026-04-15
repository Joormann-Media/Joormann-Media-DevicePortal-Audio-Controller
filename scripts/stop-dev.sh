#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/runtime/logs"
PID_FILE="$LOG_DIR/joormann-media-audio-controller.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "Keine PID-Datei gefunden: joormann-media-audio-controller"
  exit 0
fi

pid="$(cat "$PID_FILE")"
if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
  kill "$pid" 2>/dev/null || true
  sleep 1
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  echo "Gestoppt: joormann-media-audio-controller (PID $pid)"
else
  echo "Verwaiste PID-Datei entfernt: joormann-media-audio-controller"
fi

rm -f "$PID_FILE"
