#!/usr/bin/env bash
set -euo pipefail
SERVICE_NAME="joormann-media-audio-controller.service"
USER_SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

systemctl --user stop "$SERVICE_NAME" || true
systemctl --user disable "$SERVICE_NAME" || true
rm -f "$USER_SYSTEMD_DIR/$SERVICE_NAME"
systemctl --user daemon-reload
systemctl --user reset-failed "$SERVICE_NAME" || true
echo "Service entfernt: $SERVICE_NAME"
