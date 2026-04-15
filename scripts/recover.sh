#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# recover.sh  –  Hard-Reset aus Git + saubere Neuinstallation + Neustart
#
# Ablauf:
#   1. Laufenden Service stoppen
#   2. git fetch + git reset --hard origin/<branch>  (verwirft lokale Änderungen)
#   3. .venv entfernen und neu anlegen (optional: --keep-venv überspringt das)
#   4. Requirements installieren
#   5. Service neu starten
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/runtime/logs"
RECOVER_LOG="$LOG_DIR/recover.log"
VENV_DIR="$PROJECT_ROOT/.venv"

KEEP_VENV=0
for arg in "$@"; do
  case "$arg" in
    --keep-venv) KEEP_VENV=1 ;;
  esac
done

mkdir -p "$LOG_DIR"

step() { echo ""; echo "▶  $*"; echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$RECOVER_LOG"; }
ok()   { echo "   ✓  $*"; echo "[$(date '+%Y-%m-%d %H:%M:%S')] OK: $*" >> "$RECOVER_LOG"; }
fail() { echo "   ✗  $*" >&2; echo "[$(date '+%Y-%m-%d %H:%M:%S')] FAIL: $*" >> "$RECOVER_LOG"; exit 1; }

echo ""
echo "════════════════════════════════════════════════════════"
echo "  Audio Controller – Recovery"
echo "  Projekt: $PROJECT_ROOT"
echo "  Log:     $RECOVER_LOG"
echo "════════════════════════════════════════════════════════"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Recovery gestartet ===" >> "$RECOVER_LOG"

step "Service stoppen ..."
if bash "$SCRIPT_DIR/stop-dev.sh" >> "$RECOVER_LOG" 2>&1; then
  ok "Service gestoppt"
else
  echo "   (Service war nicht aktiv – wird fortgesetzt)"
fi

step "Git-Stand ermitteln ..."
cd "$PROJECT_ROOT"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  fail "Kein Git-Repository unter $PROJECT_ROOT"
fi

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
local_before="$(git rev-parse --short=12 HEAD 2>/dev/null || echo '?')"
ok "Branch: $branch  |  lokaler Stand: $local_before"

step "git fetch origin ..."
if ! git fetch origin --prune >> "$RECOVER_LOG" 2>&1; then
  fail "git fetch fehlgeschlagen – Netzwerk oder SSH-Key prüfen"
fi
ok "Fetch erfolgreich"

remote_ref="origin/${branch}"
if ! git rev-parse --verify "$remote_ref" >/dev/null 2>&1; then
  fail "Remote-Branch nicht gefunden: $remote_ref"
fi
remote_commit="$(git rev-parse --short=12 "$remote_ref" 2>/dev/null || echo '?')"

step "git reset --hard $remote_ref  (lokale Änderungen werden verworfen) ..."
git reset --hard "$remote_ref" >> "$RECOVER_LOG" 2>&1
local_after="$(git rev-parse --short=12 HEAD 2>/dev/null || echo '?')"
ok "Reset abgeschlossen  |  $local_before → $local_after"

if [[ "$KEEP_VENV" -eq 1 ]]; then
  step "Virtuelle Umgebung beibehalten (--keep-venv)"
else
  step "Virtuelle Umgebung entfernen und neu anlegen ..."
  if [[ -d "$VENV_DIR" ]]; then
    rm -rf "$VENV_DIR"
    ok ".venv entfernt"
  fi
  python3 -m venv "$VENV_DIR" >> "$RECOVER_LOG" 2>&1
  ok "Neue virtuelle Umgebung angelegt: $VENV_DIR"
fi

PYTHON_BIN="$VENV_DIR/bin/python"

step "pip install requirements ..."
"$PYTHON_BIN" -m pip install -q --upgrade pip >> "$RECOVER_LOG" 2>&1
if [[ -f "$PROJECT_ROOT/requirements.txt" ]]; then
  "$PYTHON_BIN" -m pip install -q -r "$PROJECT_ROOT/requirements.txt" >> "$RECOVER_LOG" 2>&1
  ok "Requirements installiert"
else
  echo "   (keine requirements.txt gefunden – übersprungen)"
fi

step "Service neu starten ..."
if bash "$SCRIPT_DIR/start-dev.sh" >> "$RECOVER_LOG" 2>&1; then
  ok "Service gestartet"
else
  fail "Service-Start fehlgeschlagen – siehe Log: $RECOVER_LOG"
fi

echo ""
echo "════════════════════════════════════════════════════════"
echo "  Recovery abgeschlossen"
echo "  Commit vorher : $local_before"
echo "  Commit nachher: $local_after  (remote: $remote_commit)"
echo "  Log: $RECOVER_LOG"
echo "════════════════════════════════════════════════════════"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Recovery abgeschlossen ===" >> "$RECOVER_LOG"
