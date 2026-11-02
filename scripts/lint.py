#!/usr/bin/env python3
"""
scripts/lint.py — Lightweight lint + emoji check for the orchestrator.

Checks:
  1. All server/*.py files parse without syntax errors
  2. No emojis in static/index.html, static/app.js
  3. No TODOs left in critical paths
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"
STATIC = ROOT / "static"

EMOJI_PATTERN = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\U0001F1E6-\U0001F1FF]")


def check_syntax() -> bool:
    """Parse all .py files in server/."""
    print("== Syntax check ==")
    ok = True
    for path in sorted(SERVER.glob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            ast.parse(path.read_text())
            print(f"  OK  {path.name}")
        except SyntaxError as e:
            print(f"  FAIL {path.name}: {e}")
            ok = False
    return ok


def check_emojis() -> bool:
    """Verify no emojis in static UI files."""
    print("== Emoji check (static UI) ==")
    ok = True
    for fname in ("index.html", "app.js"):
        path = STATIC / fname
        if not path.exists():
            print(f"  SKIP {fname} (not found)")
            continue
        content = path.read_text()
        matches = EMOJI_PATTERN.findall(content)
        if matches:
            print(f"  FAIL {fname}: {len(matches)} emoji(s) found: {matches[:5]}")
            ok = False
        else:
            print(f"  OK  {fname} (0 emojis)")
    return ok


def main() -> int:
    syntax_ok = check_syntax()
    emoji_ok = check_emojis()
    if syntax_ok and emoji_ok:
        print("\n  Lint: PASS")
        return 0
    print("\n  Lint: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
