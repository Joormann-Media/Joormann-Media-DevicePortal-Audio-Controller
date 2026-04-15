#!/usr/bin/env bash
set -euo pipefail
sudo systemctl stop joormann-media-audio-controller.service || true
sudo systemctl disable joormann-media-audio-controller.service || true
sudo rm -f /etc/systemd/system/joormann-media-audio-controller.service
sudo systemctl daemon-reload
sudo systemctl reset-failed joormann-media-audio-controller.service || true
echo "Service entfernt: joormann-media-audio-controller.service"
