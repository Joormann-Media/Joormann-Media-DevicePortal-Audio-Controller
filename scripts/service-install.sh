#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SYSTEMD_DIR="$PROJECT_ROOT/systemd"
SERVICE_TEMPLATE="$SYSTEMD_DIR/joormann-media-audio-controller.service"
SERVICE_NAME="joormann-media-audio-controller.service"
USER_SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_PATH="$USER_SYSTEMD_DIR/$SERVICE_NAME"

if [[ ! -f "$SERVICE_TEMPLATE" ]]; then
  echo "Service-Template fehlt: $SERVICE_TEMPLATE"
  exit 1
fi

mkdir -p "$USER_SYSTEMD_DIR"

sed "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" "$SERVICE_TEMPLATE" > "$SERVICE_PATH"
chmod 644 "$SERVICE_PATH"

systemctl --user daemon-reload

echo "Installiert: $SERVICE_NAME"
echo "Pfad:        $SERVICE_PATH"
echo "Aktivieren:  $SCRIPT_DIR/service-enable.sh"
echo "Starten:     $SCRIPT_DIR/service-start.sh"
