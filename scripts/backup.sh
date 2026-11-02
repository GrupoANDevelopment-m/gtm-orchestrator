#!/bin/bash
# backup.sh — cron-runnable backup script.
# Use:   bash scripts/backup.sh
# Cron:  0 3 * * *  /workspace/orchestrator/scripts/backup.sh
#
# Backups:
#   - orchestrator.sqlite3 -> orchestrator-YYYYMMDD-HHMMSS.sqlite3
#   - cognitive.sqlite3     -> cognitive-YYYYMMDD-HHMMSS.sqlite3
#   - dsh-meters.sqlite3    -> dsh-meter-YYYYMMDD-HHMMSS.sqlite3
#   - dsh-sessions/*.jsonl  -> dsh-sessions-YYYYMMDD-HHMMSS.tar.gz
#   - ds.sh-skills/*.md      -> dsh-skills-YYYYMMDD-HHMMSS.tar.gz
#   - keys/.nvidia_*.txt     -> NOT backed up (chmod 600 stays on disk)
#
# Retention: 30 days. Backups older than 30 days are auto-deleted.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
DATA="$ROOT/data"
BACKUP_ROOT="$ROOT/backups"
TS=$(date +%Y%m%d-%H%M%S)
TODAY="$BACKUP_ROOT/$TS"
mkdir -p "$TODAY"

echo "[backup] Starting backup at $TS"

# SQLite backups (use Python sqlite3 for crash-safe copy — no sqlite3 binary needed)
backup_sqlite() {
    local db_path="$1"
    local db_name="$2"
    python3 -c "
import sqlite3, shutil
src = sqlite3.connect('$db_path')
dst = sqlite3.connect('$TODAY/$db_name-$TS.sqlite3')
with dst:
    src.backup(dst)
src.close(); dst.close()
" 2>&1 | tail -1
    echo "[backup]   $db_name.sqlite3"
}

for db in orchestrator cognitive goals dsh-meter dsh-schedule; do
    if [ -f "$DATA/$db.sqlite3" ]; then
        backup_sqlite "$DATA/$db.sqlite3" "$db"
    fi
done

# DSH sessions (JSONL)
if [ -d "$DATA/dsh-sessions" ] && [ "$(ls -A "$DATA/dsh-sessions" 2>/dev/null)" ]; then
    tar czf "$TODAY/dsh-sessions-$TS.tar.gz" -C "$DATA" dsh-sessions
    echo "[backup]   dsh-sessions/*.jsonl"
fi

# Skills
if [ -d "$ROOT/upstream/dsh-skills" ] && [ "$(ls -A "$ROOT/upstream/dsh-skills" 2>/dev/null)" ]; then
    tar czf "$TODAY/dsh-skills-$TS.tar.gz" -C "$ROOT/upstream" dsh-skills
    echo "[backup]   dsh-skills/*.md"
fi

# Logs (recent only)
if [ -f "$DATA/orchestrator.log.jsonl" ]; then
    tail -10000 "$DATA/orchestrator.log.jsonl" > "$TODAY/orchestrator-log-tail.jsonl"
    echo "[backup]   orchestrator.log.jsonl (last 10k)"
fi

# Compute size
TOTAL=$(du -sh "$TODAY" | awk '{print $1}')
echo "[backup] Total backup size: $TOTAL at $TODAY"

# Retention: delete backups older than 30 days
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -mtime +30 -exec rm -rf {} \;
echo "[backup] Pruned backups older than 30 days"

echo "[backup] Done. Restore with: bash scripts/restore.sh $TS"
