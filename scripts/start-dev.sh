#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/runtime/logs"
PID_FILE="$LOG_DIR/joormann-media-audio-controller.pid"
LOG_FILE="$LOG_DIR/joormann-media-audio-controller.log"

CONFIG_DIR="$PROJECT_ROOT/config"
PORTS_ENV_FILE="${JARVIS_PORTS_FILE:-$CONFIG_DIR/ports.env}"
PORTS_LOCAL_FILE="${JARVIS_PORTS_LOCAL_FILE:-$CONFIG_DIR/ports.local.env}"

if [[ -f "$PORTS_ENV_FILE" ]]; then
  set -a; source "$PORTS_ENV_FILE"; set +a
fi
if [[ -f "$PORTS_LOCAL_FILE" ]]; then
  set -a; source "$PORTS_LOCAL_FILE"; set +a
fi

FLASK_HOST="${FLASK_HOST:-0.0.0.0}"
FLASK_PORT="${FLASK_PORT:-5071}"
FLASK_DEBUG="${FLASK_DEBUG:-0}"
AUTO_PORT_FALLBACK="${AUTO_PORT_FALLBACK:-1}"

ensure_audio_session_env() {
  local uid runtime_dir
  uid="$(id -u)"
  runtime_dir="/run/user/$uid"

  if [[ -z "${XDG_RUNTIME_DIR:-}" && -d "$runtime_dir" ]]; then
    export XDG_RUNTIME_DIR="$runtime_dir"
  fi
  if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && -n "${XDG_RUNTIME_DIR:-}" && -S "${XDG_RUNTIME_DIR}/bus" ]]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
  fi
  if [[ -z "${PULSE_SERVER:-}" && -n "${XDG_RUNTIME_DIR:-}" && -S "${XDG_RUNTIME_DIR}/pulse/native" ]]; then
    export PULSE_SERVER="unix:${XDG_RUNTIME_DIR}/pulse/native"
  fi
}

is_port_in_use() {
  local port="$1"
  python3 - "$port" <<'PY' >/dev/null 2>&1
import socket, sys
port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.5)
    sys.exit(0 if sock.connect_ex(("127.0.0.1", port)) == 0 else 1)
PY
}

find_next_free_port() {
  local port="$1"
  local tries=0
  while is_port_in_use "$port"; do
    port=$((port + 1))
    tries=$((tries + 1))
    if [ "$tries" -ge 200 ]; then echo ""; return 1; fi
  done
  echo "$port"
}

persist_ports_local() {
  mkdir -p "$CONFIG_DIR"
  cat > "$PORTS_LOCAL_FILE" <<EOF
FLASK_HOST=$FLASK_HOST
FLASK_PORT=$FLASK_PORT
FLASK_DEBUG=$FLASK_DEBUG
EOF
}

mkdir -p "$LOG_DIR"

VENV_DIR="$PROJECT_ROOT/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Erstelle virtuelle Umgebung: $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

if [[ -f "$PROJECT_ROOT/requirements.txt" ]]; then
  echo "Installiere/aktualisiere Requirements ..."
  "$PYTHON_BIN" -m pip install -q --upgrade pip
  "$PYTHON_BIN" -m pip install -q -r "$PROJECT_ROOT/requirements.txt"
fi

if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(cat "$PID_FILE")"
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    echo "Bereits aktiv: joormann-media-audio-controller (PID $existing_pid)"
    echo "URL: http://${FLASK_HOST}:${FLASK_PORT}"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

if is_port_in_use "$FLASK_PORT"; then
  if [ "$AUTO_PORT_FALLBACK" = "1" ] || [ "$AUTO_PORT_FALLBACK" = "true" ] || [ "$AUTO_PORT_FALLBACK" = "yes" ]; then
    next_port="$(find_next_free_port "$FLASK_PORT")"
    if [ -z "$next_port" ]; then
      echo "Port bereits belegt: ${FLASK_PORT}. Kein freier Fallback-Port gefunden."
      exit 1
    fi
    echo "Port bereits belegt: ${FLASK_PORT}. Wechsle auf freien Port: ${next_port}"
    FLASK_PORT="$next_port"
    persist_ports_local
  else
    echo "Port bereits belegt: ${FLASK_PORT}. Start abgebrochen."
    exit 1
  fi
fi

ensure_audio_session_env

(
  cd "$PROJECT_ROOT"
  nohup env \
    FLASK_HOST="$FLASK_HOST" \
    FLASK_PORT="$FLASK_PORT" \
    FLASK_DEBUG="$FLASK_DEBUG" \
    XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-}" \
    DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-}" \
    PULSE_SERVER="${PULSE_SERVER:-}" \
    "$PYTHON_BIN" app.py >>"$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
)

sleep 1
pid="$(cat "$PID_FILE")"
if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
  echo "Gestartet: joormann-media-audio-controller (PID $pid)"
  echo "URL: http://${FLASK_HOST}:${FLASK_PORT}"
  echo "Log: $LOG_FILE"
  "$SCRIPT_DIR/autodiscover.sh" || true
else
  rm -f "$PID_FILE"
  echo "Fehlgeschlagen. Siehe Log: $LOG_FILE"
  exit 1
fi
