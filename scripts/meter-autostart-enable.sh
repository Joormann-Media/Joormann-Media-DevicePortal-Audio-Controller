#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$PROJECT_ROOT/config/meter_autostart.json"

mkdir -p "$(dirname "$CONFIG_FILE")"
echo '{"enabled": true}' > "$CONFIG_FILE"
echo "Meter-Autostart aktiviert: $CONFIG_FILE"
echo "Der Pegel-Meter startet beim nächsten Service-Start automatisch."
echo "Service neu starten: $SCRIPT_DIR/stop-dev.sh && $SCRIPT_DIR/start-dev.sh"
