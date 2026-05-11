#!/usr/bin/env bash
# Daily backup for webchat: code + sqlite + notebook + actions journal.
# Keep N=14 most recent daily snapshots.
set -eu

ROOT="${WEBCHAT_DIR:-/home/exedev/webchat}"
NOTEBOOK="${NOTEBOOK_DIR:-/home/exedev/notebook}"
DEST="${BACKUP_DIR:-/home/exedev/backups}"
KEEP="${BACKUP_KEEP:-14}"
DATE="$(date +%Y-%m-%d)"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
OUT="$DEST/$STAMP"

mkdir -p "$OUT"
logfile="$OUT/backup.log"
exec >>"$logfile" 2>&1
echo "[$STAMP] backup start"

# 1) Source tarball (exclude heavy/throwaway stuff).
tar -czf "$OUT/webchat-src.tar.gz" \
    --exclude='__pycache__' \
    --exclude='.venv' \
    --exclude='workspaces' \
    --exclude='*.bak*' \
    --exclude='*.db-wal' --exclude='*.db-shm' \
    -C "$(dirname "$ROOT")" "$(basename "$ROOT")"
echo "src ok: $(du -h "$OUT/webchat-src.tar.gz" | awk '{print $1}')"

# 2) SQLite consistent snapshot via .backup (handles WAL).
DB="$ROOT/agent_sessions.db"
if [ -f "$DB" ]; then
  sqlite3 "$DB" ".backup '$OUT/agent_sessions.db'"
  echo "db ok: $(du -h "$OUT/agent_sessions.db" | awk '{print $1}')"
fi

# 3) Notebook copy.
if [ -d "$NOTEBOOK" ]; then
  tar -czf "$OUT/notebook.tar.gz" -C "$(dirname "$NOTEBOOK")" "$(basename "$NOTEBOOK")"
  echo "notebook ok"
fi

# 4) Append today's action summary to notebook JOURNAL.md.
if [ -d "$NOTEBOOK" ] && [ -f "$DB" ]; then
  TODAY_START_EPOCH=$(date -d "$DATE 00:00:00" +%s)
  SUMMARY=$(sqlite3 "$DB" <<SQL
SELECT printf('  - %s %s %s', datetime(ts,'unixepoch','localtime'), tool,
               COALESCE(file, substr(args_json,1,80)))
FROM actions
WHERE ts >= $TODAY_START_EPOCH
ORDER BY ts DESC
LIMIT 30;
SQL
)
  COUNT=$(sqlite3 "$DB" "SELECT COUNT(*) FROM actions WHERE ts >= $TODAY_START_EPOCH;")
  FAILS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM actions WHERE ts >= $TODAY_START_EPOCH AND ok=0;")
  if [ "${COUNT:-0}" -gt 0 ]; then
    {
      echo
      echo "## $DATE — auto-backup"
      echo
      echo "Actions today: $COUNT (failures: $FAILS). Snapshot: \`$OUT\`."
      echo "Last 30:"
      echo "\`\`\`"
      echo "$SUMMARY"
      echo "\`\`\`"
    } >> "$NOTEBOOK/JOURNAL.md"
    echo "journal appended"
  else
    echo "no actions today—journal not touched"
  fi
fi

# 5) Rotate — keep newest $KEEP.
cd "$DEST"
ls -1dt 20*/ 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -rf
echo "[$STAMP] done; kept newest $KEEP"
