#!/usr/bin/env bash
# Live smoke test against running webchat. Run AFTER `systemctl restart`.
set -eu
BASE="${BASE:-http://localhost:3000}"
AUTH_HDR=()
if [ -n "${KIRA_AUTH_TOKEN:-}" ]; then
  # CSV → first token
  TOK="${KIRA_AUTH_TOKEN%%,*}"
  AUTH_HDR=(-H "Authorization: Bearer $TOK")
fi
fail=0
check() {
  local name="$1" url="$2" pattern="$3"
  body=$(curl -fsS "${AUTH_HDR[@]}" "$BASE$url" || { echo "FAIL $name: HTTP"; return 1; })
  if echo "$body" | grep -q "$pattern"; then
    echo "OK   $name"
  else
    echo "FAIL $name: pattern '$pattern' not in body"
    fail=1
  fi
}
check healthz       /healthz             '"ok":true'
check models        /models              'multiplier'
check skills        /skills              'skills'
check root_html     /                    'id="plan-panel"'
check root_actions  /                    'data-nav="actions"'
check root_models   /                    'id="models-view"'
check root_settings /                    'id="settings-modal"'
# JS moved to /static/app.js after frontend split.
check root_setmodel /static/app.js        'applyModel('
check root_iframe   /static/agent_sse.js  "type === 'iframe'"
check plan_empty    /agent/plan/zzz      '"items":\[\]'
check actions_list  /agent/actions       '"actions"'
check limits        /agent/limits        'session_limit'
check hooks         /agent/hooks         '"hooks"'
check metrics       /agent/metrics       '"by_tool"'
check keys          /agent/keys          '"pool_size"'
check memory        /agent/memory        '"chunks"'
check coverage      /agent/coverage      '"ok"'
check auth          /agent/auth_status   '"runtime"'
check health        /agent/health        '"status"'
# Tool count check: agent_tool_specs.json should ship via static or be on disk.
if command -v jq >/dev/null 2>&1 && [ -f agent_tool_specs.json ]; then
  n=$(jq length agent_tool_specs.json)
  if [ "$n" -ge 38 ]; then echo "OK   tool_specs_count=$n"; else echo "FAIL tool_specs_count=$n (<38)"; fail=1; fi
fi
if [ $fail -eq 0 ]; then echo "--- ALL OK ---"; else echo "--- FAIL ---"; exit 1; fi
