"""
Browser-based social automation setup helper.

WALKS the user through the steps needed to enable browser-based actions
(no API keys required):

  1. Install Google Chrome
  2. Log in to the social platforms you want to automate
  3. (Optional) Install opencli for desktop-grade automation
  4. (Optional) Install per-platform CLI tools (twitter-cli, rdt-cli, etc.)

After setup, the orchestrator can:
  - Send DMs on Twitter/LinkedIn/Instagram via your Chrome session
  - Send emails via Gmail Web
  - Add contacts to HubSpot/Salesforce via browser
  - Send WhatsApp messages via web.whatsapp.com
  - Auto-respond to inbound messages
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


SETUP_STEPS = [
    {
        "id": "chrome",
        "name": "Install Google Chrome",
        "description": "Required for browser automation",
        "check_cmd": ["google-chrome", "--version"],
        "check_alt": ["chromium", "--version"],
        "install_linux": "wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && sudo dpkg -i google-chrome-stable_current_amd64.deb && sudo apt -f install -y",
        "install_macos": "brew install --cask google-chrome",
        "install_windows": "winget install Google.Chrome",
        "verify": "google-chrome --version",
    },
    {
        "id": "chrome_user_data",
        "name": "Verify Chrome profile exists",
        "description": "Your logged-in Chrome session lives here",
        "check_paths": [
            "~/.config/google-chrome",
            "~/Library/Application Support/Google/Chrome",
            "C:/Users/<user>/AppData/Local/Google/Chrome/User Data",
        ],
        "manual_step": "Open Chrome → log in to Gmail, Twitter, LinkedIn, etc. → close Chrome (it saves the session automatically).",
    },
    {
        "id": "opencli",
        "name": "Install opencli (recommended for desktop)",
        "description": "opencli <site> <action> -f yaml — uses your Chrome session",
        "install": "curl -fsSL https://opencli.com/install.sh | sh",
        "verify": "opencli --version",
    },
    {
        "id": "twitter_cli",
        "name": "Install twitter-cli (optional, for server-side Twitter)",
        "description": "Server-side Twitter read+write via cookies",
        "install": "pipx install twitter-cli",
        "verify": "twitter --version",
    },
    {
        "id": "rdt_cli",
        "name": "Install rdt-cli (optional, for server-side Reddit)",
        "description": "Server-side Reddit read+write via cookies",
        "install": "pipx install 'git+https://github.com/public-clis/rdt-cli.git'",
        "verify": "rdt --version",
    },
    {
        "id": "bili_cli",
        "name": "Install bili-cli (optional, for Bilibili)",
        "description": "Server-side Bilibili read+write",
        "install": "pipx install bili-cli",
        "verify": "bili --version",
    },
    {
        "id": "yt_dlp",
        "name": "Install yt-dlp (optional, for YouTube)",
        "description": "Server-side YouTube read",
        "install": "pipx install yt-dlp",
        "verify": "yt-dlp --version",
    },
    {
        "id": "gh",
        "name": "Install GitHub CLI (optional, for GitHub actions)",
        "description": "Server-side GitHub read+write",
        "install": "brew install gh  # or: apt install gh",
        "verify": "gh --version",
    },
]


def check_chrome() -> dict:
    """Check if Chrome is installed and find its user-data-dir."""
    chrome_bin = shutil.which("google-chrome") or shutil.which("chromium")
    candidates = [
        Path.home() / ".config/google-chrome",
        Path("/root/.config/google-chrome"),
        Path.home() / "AppData/Local/Google/Chrome/User Data",
    ]
    user_data_dir = None
    for c in candidates:
        if c.exists():
            user_data_dir = str(c)
            break
    return {
        "chrome_installed": bool(chrome_bin),
        "chrome_path": chrome_bin,
        "user_data_dir": user_data_dir,
        "ready": bool(chrome_bin and user_data_dir),
    }


def check_opencli() -> bool:
    return bool(shutil.which("opencli") or shutil.which("OpenCLI"))


def check_all() -> dict:
    """Check the full browser-automation setup status."""
    chrome = check_chrome()
    opencli = check_opencli()
    return {
        "chrome": chrome,
        "opencli_installed": opencli,
        "ready_for_browser_automation": chrome["ready"],
        "ready_for_opencli_automation": opencli and chrome["ready"],
        "next_steps": _next_steps(chrome, opencli),
    }


def _next_steps(chrome_status: dict, opencli: bool) -> list[str]:
    """Return ordered list of setup steps to take."""
    steps = []
    if not chrome_status["chrome_installed"]:
        steps.append("Install Google Chrome (see setup_steps.chrome)")
    if not chrome_status["user_data_dir"]:
        steps.append("Open Chrome once so the user profile is created")
    if not opencli and chrome_status["ready"]:
        steps.append("Install opencli for desktop-grade browser automation")
    if chrome_status["ready"]:
        steps.append("Log in to the social platforms you want to automate "
                     "(Gmail, Twitter, LinkedIn, Instagram, etc.)")
        steps.append("Restart the orchestrator so it picks up the Chrome profile")
    return steps


SETUP_INSTRUCTIONS = """
BROWSER AUTOMATION SETUP
========================

To enable browser-based social/CRM actions (no API keys needed), do this:

1. INSTALL GOOGLE CHROME
   Linux:  wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
           sudo dpkg -i google-chrome-stable_current_amd64.deb
   macOS:  brew install --cask google-chrome
   Win:    winget install Google.Chrome

2. LOG IN to the social platforms you want to automate
   - Gmail
   - Twitter / X
   - LinkedIn
   - Instagram
   - Facebook
   - Reddit
   - HubSpot (app.hubspot.com)
   - Salesforce (your instance)
   - WhatsApp Web (web.whatsapp.com)
   - Discord Web (discord.com/app)
   - Telegram Web (web.telegram.org)

   Tip: log in once, then CLOSE Chrome. The session is saved to your user-data-dir.

3. (Optional but recommended) INSTALL opencli
   curl -fsSL https://opencli.com/install.sh | sh

4. RESTART the orchestrator
   bash bootstrap.sh
   # or
   pkill -9 -f "python3.*api" && cd /workspace/orchestrator && nohup python3 -B server/api.py 8000 &

5. VERIFY
   curl http://localhost:8000/api/browser/channels -H "Authorization: Bearer $API_KEY"
   # Should show channels with status "ready" instead of "unavailable"

WHAT YOU CAN DO WITHOUT ANY API KEYS:
  - Send DMs on Twitter/LinkedIn/Instagram (via your Chrome)
  - Send emails via Gmail Web
  - Add contacts to HubSpot/Salesforce via browser
  - Send WhatsApp messages via web.whatsapp.com
  - Auto-respond to inbound messages
  - Cross-post content across platforms
  - Auto-engage with target accounts (likes/comments)
  - Run cron jobs: crm-hygiene, social-listening, etc.
"""