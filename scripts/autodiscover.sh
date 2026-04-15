#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_DIR="$PROJECT_ROOT/config"
PORTS_ENV_FILE="${JARVIS_PORTS_FILE:-$CONFIG_DIR/ports.env}"
PORTS_LOCAL_FILE="${JARVIS_PORTS_LOCAL_FILE:-$CONFIG_DIR/ports.local.env}"

if [[ -f "$PORTS_ENV_FILE" ]]; then
  set -a; source "$PORTS_ENV_FILE"; set +a
fi
if [[ -f "$PORTS_LOCAL_FILE" ]]; then
  set -a; source "$PORTS_LOCAL_FILE"; set +a
fi

PORTAL_AUTODISCOVER_URL="${PORTAL_AUTODISCOVER_URL:-${AUTODISCOVER_URL:-}}"
if [[ -z "$PORTAL_AUTODISCOVER_URL" ]]; then
  PORTAL_HOST="${PORTAL_HOST:-127.0.0.1}"
  PORTAL_PORT="${PORTAL_PORT:-5070}"
  PORTAL_AUTODISCOVER_URL="http://${PORTAL_HOST}:${PORTAL_PORT}/autodiscover"
fi

if ! command -v curl >/dev/null 2>&1; then exit 0; fi

repo_name="$(basename "$PROJECT_ROOT")"
repo_link=""
repo_branch="${AUTODISCOVER_REPO_BRANCH:-main}"
if command -v git >/dev/null 2>&1; then
  repo_link="$(git -C "$PROJECT_ROOT" remote get-url origin 2>/dev/null || true)"
  branch_guess="$(git -C "$PROJECT_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  if [[ -n "$branch_guess" && "$branch_guess" != "HEAD" ]]; then
    repo_branch="$branch_guess"
  fi
fi
if [[ "$repo_link" =~ ^git@github.com:(.+)$ ]]; then
  repo_link="https://github.com/${BASH_REMATCH[1]}"
fi
if [[ -n "$repo_link" && ! "$repo_link" =~ \.git$ ]]; then
  repo_link="${repo_link}.git"
fi

service_name="${AUTODISCOVER_SERVICE_NAME:-joormann-media-audio-controller.service}"
service_id="${AUTODISCOVER_SERVICE_ID:-}"
if [[ -z "$service_id" ]]; then
  seed="${repo_link:-$repo_name}|$service_name"
  if command -v sha1sum >/dev/null 2>&1; then
    service_id="jsvc_$(printf '%s' "$seed" | sha1sum | awk '{print $1}' | cut -c1-12)"
  else
    service_id="jsvc_$(printf '%s' "$seed" | md5sum | awk '{print $1}' | cut -c1-12)"
  fi
fi
service_user="${AUTODISCOVER_SERVICE_USER:-${USER:-}}"
install_dir="${AUTODISCOVER_INSTALL_DIR:-$PROJECT_ROOT}"
use_service="${AUTODISCOVER_USE_SERVICE:-true}"
autostart="${AUTODISCOVER_AUTOSTART:-true}"

service_port="${AUTODISCOVER_PORT:-${FLASK_PORT:-5071}}"
lan_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
host_ip="${AUTODISCOVER_HOST_IP:-${lan_ip:-127.0.0.1}}"

api_base_url="${AUTODISCOVER_API_BASE_URL:-}"
if [[ -z "$api_base_url" && -n "$service_port" ]]; then
  api_base_url="http://${host_ip}:${service_port}"
fi
health_url="${AUTODISCOVER_HEALTH_URL:-}"
if [[ -z "$health_url" && -n "$api_base_url" ]]; then
  health_url="${api_base_url}/health"
fi
ui_url="${AUTODISCOVER_UI_URL:-}"
if [[ -z "$ui_url" && -n "$api_base_url" ]]; then
  ui_url="${api_base_url}/"
fi

node_name="${AUTODISCOVER_NODE_NAME:-$(hostname 2>/dev/null || true)}"
instance_id="${AUTODISCOVER_INSTANCE_ID:-${node_name}-${repo_name}}"

payload="$(python3 - <<PY
import json

def to_bool(v):
    return str(v).strip().lower() in {"1", "true", "yes", "on"}

raw_port = ${service_port@Q}
port = int(raw_port) if str(raw_port).isdigit() else None
api_base_url = ${api_base_url@Q}
health_url = ${health_url@Q}
ui_url = ${ui_url@Q}
print(json.dumps({
  "repo_name": ${repo_name@Q},
  "repo_link": ${repo_link@Q},
  "repo_branch": ${repo_branch@Q},
  "install_dir": ${install_dir@Q},
  "service_name": ${service_name@Q},
  "service_id": ${service_id@Q},
  "service_user": ${service_user@Q},
  "use_service": to_bool(${use_service@Q}),
  "autostart": to_bool(${autostart@Q}),
  "service_port": port,
  "api_base_url": api_base_url,
  "health_url": health_url,
  "ui_url": ui_url,
  "endpoints": {"api_base": api_base_url, "health": health_url, "ui": ui_url},
  "hostname": ${node_name@Q},
  "node_name": ${node_name@Q},
  "instance_id": ${instance_id@Q},
  "tags": ["jarvis", "autodiscover", "audio"],
  "capabilities": ["audio.play", "audio.stream", "audio.spotify", "audio.volume", "audio.bluetooth"],
}))
PY
)"

curl -fsS --max-time 4 --connect-timeout 2 \
  -H "Content-Type: application/json" \
  -X POST "$PORTAL_AUTODISCOVER_URL" \
  --data-binary "$payload" >/dev/null 2>&1 || true
