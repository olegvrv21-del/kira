#!/usr/bin/env bash
# Polls Kira /agent/health and sends a Telegram alert when status != ok.
#
# Required env (sourced from $HEALTH_ALERT_ENV or /etc/kira/health_alert.env):
#   KIRA_URL           e.g. http://localhost:3000
#   KIRA_AUTH_TOKEN    bearer token
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
#
# State file (last status) at /tmp/kira_health_state. Alerts only on transitions
# ok -> degraded/critical and on recovery degraded/critical -> ok (avoid spam).
set -euo pipefail

ENV_FILE="${HEALTH_ALERT_ENV:-/etc/kira/health_alert.env}"
[[ -f "$ENV_FILE" ]] && set -a && . "$ENV_FILE" && set +a

KIRA_URL="${KIRA_URL:-http://localhost:3000}"
STATE=/tmp/kira_health_state
PREV="$(cat "$STATE" 2>/dev/null || echo unknown)"

resp=$(curl -fsS -m 10 \
  -H "Authorization: Bearer ${KIRA_AUTH_TOKEN:-}" \
  "${KIRA_URL}/agent/health" 2>/dev/null || echo '{"status":"unreachable"}')

status=$(printf '%s' "$resp" | grep -oE '"status":"[a-z]+"' | head -1 | cut -d'"' -f4)
status="${status:-unreachable}"
echo "$status" > "$STATE"

send() {
  [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_CHAT_ID:-}" ]] && {
    echo "[warn] TG creds missing, skipping send" >&2
    return 0
  }
  curl -fsS -m 10 \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d chat_id="${TELEGRAM_CHAT_ID}" \
    -d parse_mode=Markdown \
    --data-urlencode text="$1" >/dev/null
}

transition() {
  case "$PREV → $status" in
    "ok → degraded"|"ok → critical"|"unknown → critical"|"unknown → degraded"|"ok → unreachable"|"unknown → unreachable")
      send "🔴 *Кира:* ${PREV} → *${status}*%0A\`\`\`%0A$(printf '%s' "$resp" | head -c 600)%0A\`\`\`"
      ;;
    "degraded → ok"|"critical → ok"|"unreachable → ok")
      send "✅ *Кира:* восстановлена (${PREV} → ok)"
      ;;
    *)
      # no-op (same status or non-actionable transition)
      ;;
  esac
}

transition
