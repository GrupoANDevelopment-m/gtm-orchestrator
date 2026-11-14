"""
paths.py — Central path resolution for the orchestrator.

Resolves the project root relative to this file so the system works
regardless of where it is checked out (/workspace/orchestrator locally,
/home/runner/work/<repo>/<repo> in CI, anywhere in Docker, etc).

All modules should import from here instead of hardcoding absolute paths.
"""
from __future__ import annotations

import os
from pathlib import Path

# server/paths.py -> server/ -> project root
SERVER_DIR = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("ORCHESTRATOR_ROOT", SERVER_DIR.parent)).resolve()

KEYS_DIR = ROOT / "keys"
DATA_DIR = ROOT / "data"
UPSTREAM_DIR = ROOT / "upstream"
STATIC_DIR = ROOT / "static"
SCRIPTS_DIR = ROOT / "scripts"
BACKUPS_DIR = ROOT / "backups"
SKILLS_DIR = ROOT / "skills"

# Key files
VOICE_KEY_PATH = KEYS_DIR / ".nvidia_voice.txt"
MAIN_KEY_PATH = KEYS_DIR / ".nvidia_main.txt"
M2_KEY_PATH = KEYS_DIR / ".nvidia_m2.txt"
STEP_KEY_PATH = KEYS_DIR / ".nvidia_step.txt"
API_KEY_PATH = KEYS_DIR / ".orchestrator_api_key.txt"
WEBHOOK_SIGNING_KEY_PATH = KEYS_DIR / ".webhook_signing_key.txt"
TELEGRAM_TOKEN_PATH = KEYS_DIR / ".telegram_bot_token.txt"
TELEGRAM_CHAT_PATH = KEYS_DIR / ".telegram_chat_id.txt"

# Data files
LOG_FILE = DATA_DIR / "orchestrator.log.jsonl"
DB_PATH = DATA_DIR / "orchestrator.sqlite3"
BROWSER_POOL_DIR = DATA_DIR / "browser-pool"


def ensure_dirs() -> None:
    """Create the runtime directories if they don't exist."""
    for d in (KEYS_DIR, DATA_DIR, BACKUPS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def read_key(path: Path) -> str:
    """Read a secret from a file, returning '' if missing/unreadable."""
    try:
        return path.read_text().strip()
    except Exception:
        return ""


__all__ = [
    "SERVER_DIR", "ROOT", "KEYS_DIR", "DATA_DIR", "UPSTREAM_DIR",
    "STATIC_DIR", "SCRIPTS_DIR", "BACKUPS_DIR", "SKILLS_DIR",
    "VOICE_KEY_PATH", "MAIN_KEY_PATH", "M2_KEY_PATH", "STEP_KEY_PATH",
    "API_KEY_PATH", "WEBHOOK_SIGNING_KEY_PATH", "TELEGRAM_TOKEN_PATH",
    "TELEGRAM_CHAT_PATH", "LOG_FILE", "DB_PATH", "BROWSER_POOL_DIR",
    "ensure_dirs", "read_key",
]
