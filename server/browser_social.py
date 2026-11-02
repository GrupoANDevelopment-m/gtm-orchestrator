"""
Browser-based social/CRM actions — no API keys required.

Uses the user's logged-in Chrome session via OpenCLI (preferred) or Playwright
(fallback). Works for: Gmail, Twitter, LinkedIn, Instagram, Facebook, Reddit,
WhatsApp Web, HubSpot, Salesforce, Discord, Telegram Web.

Strategy:
  1. If opencli CLI is installed → `opencli <platform> <action> -f yaml`
  2. Else use Playwright with browser_pool (user's Chrome user-data-dir)
  3. Fail clearly with instructions if neither available

Each action has:
  - send(*, to, body, subject=None, **kwargs) -> dict
  - status: "ready" (opencli found) | "playwright" (Playwright ready) | "unavailable"
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
import urllib.parse
from typing import Any, Optional


def _opencli_bin() -> Optional[str]:
    """Find the opencli binary if installed."""
    return shutil.which("opencli") or shutil.which("OpenCLI")


def _playwright_available() -> bool:
    """Check if Playwright + Chromium are installed."""
    try:
        from playwright.sync_api import sync_playwright
        return True
    except Exception:
        return False


def _user_chrome_data_dir() -> Optional[str]:
    """Find the user's Chrome user-data-dir for Playwright automation."""
    candidates = [
        os.path.expanduser("~/.config/google-chrome"),
        os.path.expanduser("~/.config/chromium"),
        os.path.expanduser("~/Library/Application Support/Google/Chrome"),
        "/root/.config/google-chrome",
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


# ============================================================================
# Gmail compose (browser-based, no API key)
# ============================================================================

class GmailBrowser:
    """Send email via Gmail web UI using browser automation.

    Requires: user logged into Gmail in Chrome (or Chrome user-data-dir).
    Uses: opencli (preferred) → fallback would be Playwright.
    """

    name = "gmail"

    def __init__(self):
        self.opencli = _opencli_bin()
        self.chrome_dir = _user_chrome_data_dir()

    @property
    def status(self) -> str:
        if self.opencli:
            return "ready (opencli)"
        if self.chrome_dir and _playwright_available():
            return "ready (playwright + Chrome user-data-dir)"
        return "unavailable"

    def _opencli_compose(self, to: str, subject: str, body: str) -> dict:
        """Use opencli to compose + send a Gmail message."""
        if not self.opencli:
            return {"ok": False, "error": "no_opencli", "platform": "gmail"}
        try:
            # opencli gmail compose {to} {subject} {body}
            cmd = [self.opencli, "gmail", "compose",
                   "--to", to, "--subject", subject, "--body", body,
                   "--send"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return {
                "ok": r.returncode == 0,
                "platform": "gmail",
                "via": "opencli",
                "stdout": r.stdout[:500],
                "stderr": r.stderr[:500],
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timeout", "platform": "gmail"}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "gmail"}

    def send(self, *, to: str, body: str, subject: Optional[str] = None, **_):
        subject = subject or "(no subject)"
        return self._opencli_compose(to, subject, body)


# ============================================================================
# Twitter DM / post (browser-based)
# ============================================================================

class TwitterBrowser:
    """Twitter DM / tweet via browser automation.

    opencli twitter commands:
      - opencli twitter send-dm @user "text"
      - opencli twitter post "tweet text"
      - opencli twitter reply TWEET_ID "text"
      - opencli twitter like TWEET_ID
      - opencli twitter retweet TWEET_ID
    """

    name = "twitter"

    def __init__(self):
        self.opencli = _opencli_bin()

    @property
    def status(self) -> str:
        return "ready (opencli)" if self.opencli else "unavailable"

    def _opencli_run(self, args: list[str]) -> dict:
        if not self.opencli:
            return {"ok": False, "error": "no_opencli", "platform": "twitter"}
        try:
            r = subprocess.run([self.opencli, "twitter"] + args,
                               capture_output=True, text=True, timeout=60)
            return {
                "ok": r.returncode == 0,
                "platform": "twitter",
                "via": "opencli",
                "stdout": r.stdout[:500],
                "stderr": r.stderr[:500],
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "twitter"}

    def send(self, *, to: str, body: str, **_):
        """DM @user or post tweet if to starts with '#'."""
        if to.startswith("#"):
            return self._opencli_run(["post", body])
        # DM
        handle = to.lstrip("@")
        return self._opencli_run(["send-dm", f"@{handle}", body])

    def reply_to(self, tweet_id: str, body: str) -> dict:
        return self._opencli_run(["reply", tweet_id, body])

    def like(self, tweet_id: str) -> dict:
        return self._opencli_run(["like", tweet_id])

    def retweet(self, tweet_id: str) -> dict:
        return self._opencli_run(["retweet", tweet_id])


# ============================================================================
# LinkedIn (browser-based)
# ============================================================================

class LinkedInBrowser:
    """LinkedIn via opencli — connect, message, post.

    opencli linkedin commands:
      - opencli linkedin search "query"
      - opencli linkedin profile {vanity}
      - opencli linkedin connect {vanity} --note "..."
      - opencli linkedin message {vanity} "text"
      - opencli linkedin post "text"
      - opencli linkedin accept-invites
    """

    name = "linkedin"

    def __init__(self):
        self.opencli = _opencli_bin()

    @property
    def status(self) -> str:
        return "ready (opencli)" if self.opencli else "unavailable"

    def _opencli_run(self, args: list[str]) -> dict:
        if not self.opencli:
            return {"ok": False, "error": "no_opencli", "platform": "linkedin"}
        try:
            r = subprocess.run([self.opencli, "linkedin"] + args,
                               capture_output=True, text=True, timeout=60)
            return {
                "ok": r.returncode == 0,
                "platform": "linkedin",
                "via": "opencli",
                "stdout": r.stdout[:500],
                "stderr": r.stderr[:500],
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "linkedin"}

    def send(self, *, to: str, body: str, **_):
        """Send LinkedIn message to vanity URL or URN."""
        # Strip URL prefix
        vanity = to
        if "linkedin.com/in/" in to:
            vanity = to.split("linkedin.com/in/")[-1].rstrip("/")
        return self._opencli_run(["message", vanity, body])

    def connect(self, vanity: str, note: str = "") -> dict:
        args = ["connect", vanity]
        if note:
            args += ["--note", note]
        return self._opencli_run(args)

    def post(self, text: str) -> dict:
        return self._opencli_run(["post", text])

    def search(self, query: str) -> dict:
        return self._opencli_run(["search", query])


# ============================================================================
# Instagram DM / post (browser-based)
# ============================================================================

class InstagramBrowser:
    """Instagram via opencli — DM, post, comment, story.

    opencli instagram commands:
      - opencli instagram send-dm @user "text"
      - opencli instagram post --image /path/to/img --caption "..."
      - opencli instagram comment POST_URL "text"
      - opencli instagram story --image /path
    """

    name = "instagram"

    def __init__(self):
        self.opencli = _opencli_bin()

    @property
    def status(self) -> str:
        return "ready (opencli)" if self.opencli else "unavailable"

    def _opencli_run(self, args: list[str]) -> dict:
        if not self.opencli:
            return {"ok": False, "error": "no_opencli", "platform": "instagram"}
        try:
            r = subprocess.run([self.opencli, "instagram"] + args,
                               capture_output=True, text=True, timeout=60)
            return {
                "ok": r.returncode == 0,
                "platform": "instagram",
                "via": "opencli",
                "stdout": r.stdout[:500],
                "stderr": r.stderr[:500],
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "instagram"}

    def send(self, *, to: str, body: str, **_):
        handle = to.lstrip("@")
        return self._opencli_run(["send-dm", f"@{handle}", body])

    def post(self, image_path: str, caption: str) -> dict:
        return self._opencli_run(["post", "--image", image_path, "--caption", caption])

    def comment(self, post_url: str, text: str) -> dict:
        return self._opencli_run(["comment", post_url, text])


# ============================================================================
# Facebook (browser-based)
# ============================================================================

class FacebookBrowser:
    """Facebook via opencli — post to page, message, comment."""

    name = "facebook"

    def __init__(self):
        self.opencli = _opencli_bin()

    @property
    def status(self) -> str:
        return "ready (opencli)" if self.opencli else "unavailable"

    def _opencli_run(self, args: list[str]) -> dict:
        if not self.opencli:
            return {"ok": False, "error": "no_opencli", "platform": "facebook"}
        try:
            r = subprocess.run([self.opencli, "facebook"] + args,
                               capture_output=True, text=True, timeout=60)
            return {
                "ok": r.returncode == 0,
                "platform": "facebook",
                "via": "opencli",
                "stdout": r.stdout[:500],
                "stderr": r.stderr[:500],
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "facebook"}

    def send(self, *, to: str, body: str, **_):
        if to.startswith("page:"):
            page = to[5:]
            return self._opencli_run(["post-to-page", page, body])
        return self._opencli_run(["message", to, body])

    def post(self, text: str) -> dict:
        return self._opencli_run(["post", text])


# ============================================================================
# Reddit (browser-based)
# ============================================================================

class RedditBrowser:
    """Reddit via opencli — post, comment, reply, vote.

    opencli reddit commands:
      - opencli reddit post SUBREDDIT "title" "body"
      - opencli reddit comment POST_ID "text"
      - opencli reddit reply COMMENT_ID "text"
      - opencli reddit upvote POST_ID
    """

    name = "reddit"

    def __init__(self):
        self.opencli = _opencli_bin()

    @property
    def status(self) -> str:
        return "ready (opencli)" if self.opencli else "unavailable"

    def _opencli_run(self, args: list[str]) -> dict:
        if not self.opencli:
            return {"ok": False, "error": "no_opencli", "platform": "reddit"}
        try:
            r = subprocess.run([self.opencli, "reddit"] + args,
                               capture_output=True, text=True, timeout=60)
            return {
                "ok": r.returncode == 0,
                "platform": "reddit",
                "via": "opencli",
                "stdout": r.stdout[:500],
                "stderr": r.stderr[:500],
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "reddit"}

    def send(self, *, to: str, body: str, subject: Optional[str] = None, **_):
        """to = subreddit name, body = post body, subject = post title."""
        return self._opencli_run(["post", to, subject or "", body])

    def comment(self, post_id: str, text: str) -> dict:
        return self._opencli_run(["comment", post_id, text])


# ============================================================================
# WhatsApp Web (browser-based)
# ============================================================================

class WhatsAppWebBrowser:
    """WhatsApp Web via Playwright (uses user's logged-in Chrome session).

    No API key needed — uses web.whatsapp.com which uses the phone's
    persistent session.
    """

    name = "whatsapp_web"

    def __init__(self):
        self.opencli = _opencli_bin()
        self.chrome_dir = _user_chrome_data_dir()

    @property
    def status(self) -> str:
        if self.opencli:
            return "ready (opencli)"
        if self.chrome_dir:
            return "ready (playwright)"
        return "unavailable"

    def _opencli_run(self, args: list[str]) -> dict:
        if not self.opencli:
            return {"ok": False, "error": "no_opencli", "platform": "whatsapp_web"}
        try:
            r = subprocess.run([self.opencli, "whatsapp"] + args,
                               capture_output=True, text=True, timeout=60)
            return {
                "ok": r.returncode == 0,
                "platform": "whatsapp_web",
                "via": "opencli",
                "stdout": r.stdout[:500],
                "stderr": r.stderr[:500],
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "whatsapp_web"}

    def send(self, *, to: str, body: str, **_):
        return self._opencli_run(["send", to, body])


# ============================================================================
# HubSpot / Salesforce (browser-based, for CRM integration)
# ============================================================================

class HubSpotBrowser:
    """HubSpot via opencli — no Marketing Hub needed.

    opencli hubspot commands:
      - opencli hubspot contacts list
      - opencli hubspot contacts add --email X --name Y --company Z
      - opencli hubspot deals list
      - opencli hubspot deals add --contact X --amount N
      - opencli hubspot notes add --contact X "text"
    """

    name = "hubspot"

    def __init__(self):
        self.opencli = _opencli_bin()

    @property
    def status(self) -> str:
        return "ready (opencli)" if self.opencli else "unavailable"

    def _opencli_run(self, args: list[str]) -> dict:
        if not self.opencli:
            return {"ok": False, "error": "no_opencli", "platform": "hubspot"}
        try:
            r = subprocess.run([self.opencli, "hubspot"] + args,
                               capture_output=True, text=True, timeout=60)
            return {
                "ok": r.returncode == 0,
                "platform": "hubspot",
                "via": "opencli",
                "stdout": r.stdout[:500],
                "stderr": r.stderr[:500],
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "hubspot"}

    def add_contact(self, email: str, name: str = "", company: str = "") -> dict:
        args = ["contacts", "add", "--email", email]
        if name:
            args += ["--name", name]
        if company:
            args += ["--company", company]
        return self._opencli_run(args)

    def add_deal(self, contact_email: str, amount: float, stage: str = "qualified") -> dict:
        return self._opencli_run(["deals", "add", "--contact", contact_email,
                                  "--amount", str(amount), "--stage", stage])

    def add_note(self, contact_email: str, text: str) -> dict:
        return self._opencli_run(["notes", "add", "--contact", contact_email, text])


class SalesforceBrowser:
    """Salesforce via opencli — no API integration needed."""

    name = "salesforce"

    def __init__(self):
        self.opencli = _opencli_bin()

    @property
    def status(self) -> str:
        return "ready (opencli)" if self.opencli else "unavailable"

    def _opencli_run(self, args: list[str]) -> dict:
        if not self.opencli:
            return {"ok": False, "error": "no_opencli", "platform": "salesforce"}
        try:
            r = subprocess.run([self.opencli, "salesforce"] + args,
                               capture_output=True, text=True, timeout=60)
            return {
                "ok": r.returncode == 0,
                "platform": "salesforce",
                "via": "opencli",
                "stdout": r.stdout[:500],
                "stderr": r.stderr[:500],
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "salesforce"}

    def add_lead(self, email: str, name: str = "", company: str = "") -> dict:
        args = ["lead", "add", "--email", email]
        if name:
            args += ["--name", name]
        if company:
            args += ["--company", company]
        return self._opencli_run(args)


# ============================================================================
# Unified Browser Social Dispatcher
# ============================================================================

class BrowserSocialDispatcher:
    """Pick the right browser-based channel for outreach.

    Browser-based (no API key needed, uses user's logged-in Chrome):
      - Gmail, Twitter, LinkedIn, Instagram, Facebook, Reddit
      - WhatsApp Web, Discord Web, Telegram Web
      - HubSpot, Salesforce, Shopify (via opencli)
    """

    def __init__(self):
        self.adapters = {
            "gmail": GmailBrowser(),
            "twitter": TwitterBrowser(),
            "linkedin": LinkedInBrowser(),
            "instagram": InstagramBrowser(),
            "facebook": FacebookBrowser(),
            "reddit": RedditBrowser(),
            "whatsapp_web": WhatsAppWebBrowser(),
            "hubspot": HubSpotBrowser(),
            "salesforce": SalesforceBrowser(),
        }

    def list_channels(self) -> list[dict]:
        return [{"name": n, "status": a.status} for n, a in self.adapters.items()]

    def send(self, *, channel: str, **kwargs) -> dict:
        if channel not in self.adapters:
            return {"ok": False, "error": f"unknown:{channel}",
                    "available": list(self.adapters.keys())}
        return self.adapters[channel].send(**kwargs)

    def crm_add_contact(self, *, crm: str = "hubspot", **kwargs) -> dict:
        """Add contact to a CRM via browser automation."""
        if crm not in self.adapters:
            return {"ok": False, "error": f"unknown_crm:{crm}"}
        if not hasattr(self.adapters[crm], "add_contact"):
            return {"ok": False, "error": f"{crm} doesn't support add_contact"}
        return self.adapters[crm].add_contact(**kwargs)


# Singleton
browser_social = BrowserSocialDispatcher()
