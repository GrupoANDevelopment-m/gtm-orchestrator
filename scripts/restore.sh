#!/bin/bash
# restore.sh — restore from a backup.
# Use:   bash scripts/restore.sh 20260101-030000
# Will restore:
#   - SQLite databases (atomic move)
#   - DSH sessions + skills tarballs
# Stops/starts the server during restore.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
DATA="$ROOT/data"
BACKUP_ROOT="$ROOT/backups"

if [ -z "$1" ]; then
    echo "Usage: bash scripts/restore.sh <TS>"
    echo "Available backups:"
    ls -1 "$BACKUP_ROOT" 2>/dev/null || echo "  (none)"
    exit 1
fi

TS="$1"
SRC="$BACKUP_ROOT/$TS"
if [ ! -d "$SRC" ]; then
    echo "[restore] ERROR: backup $TS not found at $SRC"
    exit 1
fi

echo "[restore] WARNING: this will overwrite current data with backup $TS"
echo "[restore] Press Ctrl-C to abort, Enter to continue"
read -r

# Stop server
echo "[restore] Stopping orchestrator server..."
pkill -f "server/api.py" 2>/dev/null || true
sleep 2

# Move current data aside (so restore is reversible)
SAVED="$DATA/.pre-restore-$$"
mv "$DATA" "$SAVED"
mkdir -p "$DATA"

# Restore SQLite
for db in orchestrator cognitive goals dsh-meter dsh-schedule; do
    if [ -f "$SRC/$db-$TS.sqlite3" ]; then
        cp "$SRC/$db-$TS.sqlite3" "$DATA/$db.sqlite3"
        echo "[restore]   $db.sqlite3"
    fi
done

# Restore DSH sessions
if [ -f "$SRC/dsh-sessions-$TS.tar.gz" ]; then
    tar xzf "$SRC/dsh-sessions-$TS.tar.gz" -C "$DATA"
    echo "[restore]   dsh-sessions/"
fi

# Restore skills
if [ -f "$SRC/dsh-skills-$TS.tar.gz" ]; then
    mkdir -p "$ROOT/upstream"
    tar xzf "$SRC/dsh-skills-$TS.tar.gz" -C "$ROOT/upstream"
    echo "[restore]   dsh-skills/"
fi

# Restart server
echo "[restore] Restarting orchestrator server..."
cd "$ROOT" && nohup python3 -B server/api.py 8000 > server.log 2>&1 &
disown
sleep 3

echo "[restore] Done. Previous data saved at: $SAVED"
echo "[restore] Verify health: curl http://127.0.0.1:8000/health"
