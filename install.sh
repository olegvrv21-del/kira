#!/usr/bin/env bash
# Kira — one-shot installer
#
# Modes:
#   ./install.sh docker    — build & run via docker-compose (recommended)
#   ./install.sh venv      — create venv + install requirements (manual run)
#   ./install.sh systemd   — venv mode + install systemd unit (production)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

log() { printf '\033[36m[kira]\033[0m %s\n' "$*"; }
err() { printf '\033[31m[err]\033[0m %s\n' "$*" >&2; }

ensure_env() {
  if [[ ! -f .env ]]; then
    log "Creating .env from .env.example — EDIT IT BEFORE RUNNING"
    cp .env.example .env
    if command -v openssl >/dev/null 2>&1; then
      local tok
      tok="ktk_$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)"
      sed -i.bak "s|KIRA_AUTH_TOKEN=.*|KIRA_AUTH_TOKEN=${tok}|" .env && rm -f .env.bak
      log "Generated KIRA_AUTH_TOKEN=${tok}"
    fi
    err "Open .env and set KIRO_API_KEY, then re-run."
    exit 1
  fi
}

mode="${1:-docker}"
case "$mode" in
  docker)
    ensure_env
    command -v docker >/dev/null 2>&1 || { err "docker not installed"; exit 1; }
    log "Building image…"
    docker compose build
    log "Starting…"
    docker compose up -d
    sleep 3
    log "Health: $(curl -fsS http://localhost:3000/healthz 2>&1 || echo unreachable)"
    log "✅ Kira is up at http://localhost:3000/"
    log "   Token: $(grep ^KIRA_AUTH_TOKEN .env | cut -d= -f2)"
    ;;
  venv)
    ensure_env
    [[ -d .venv ]] || python3 -m venv .venv
    .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q -r requirements.txt
    log "✅ venv ready. Run:"
    log "   source .env && .venv/bin/uvicorn app:app --host 0.0.0.0 --port 3000"
    ;;
  systemd)
    "$0" venv
    UNIT=/etc/systemd/system/kira.service
    log "Installing $UNIT (sudo)…"
    sudo tee "$UNIT" >/dev/null <<UNIT
[Unit]
Description=Kira Web Chat
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$ROOT
EnvironmentFile=$ROOT/.env
ExecStart=$ROOT/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 3000
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
    sudo systemctl daemon-reload
    sudo systemctl enable --now kira
    log "✅ kira.service started. Check: systemctl status kira"
    ;;
  *)
    cat <<USAGE
Usage: $0 <mode>

Modes:
  docker     build & run via docker compose (recommended)
  venv       create Python venv and install deps
  systemd    venv mode + register systemd unit
USAGE
    exit 1
    ;;
esac
