#!/usr/bin/env bash
# Live smoke test against running webchat. Run AFTER `systemctl restart`.
set -eu
BASE="${BASE:-http://localhost:3000}"
fail=0
check() {
  local name="$1" url="$2" pattern="$3"
  body=$(curl -fsS "$BASE$url" || { echo "FAIL $name: HTTP"; return 1; })
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
check root_setmodel /                    'applyModel('
check root_iframe   /                    "type === 'iframe'"
check plan_empty    /agent/plan/zzz      '"items":\[\]'
check actions_list  /agent/actions       '"actions"'
check limits        /agent/limits        'session_limit'
check hooks         /agent/hooks         '"hooks"'
check metrics       /agent/metrics       '"by_tool"'
check keys          /agent/keys          '"pool_size"'
# Tool count check: agent_tool_specs.json should ship via static or be on disk.
if command -v jq >/dev/null 2>&1 && [ -f agent_tool_specs.json ]; then
  n=$(jq length agent_tool_specs.json)
  if [ "$n" -ge 34 ]; then echo "OK   tool_specs_count=$n"; else echo "FAIL tool_specs_count=$n (<34)"; fail=1; fi
fi
if [ $fail -eq 0 ]; then echo "--- ALL OK ---"; else echo "--- FAIL ---"; exit 1; fi
