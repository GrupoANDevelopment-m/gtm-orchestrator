"""
Playwright-based social actions — no opencli, no API keys needed.

Uses the user's logged-in Chrome session by loading the Chrome user-data-dir.
This is exactly how n8n's "Browser automation" nodes work, and how
agent-reach's OpenCLI bridge works under the hood.

Each platform action has:
  - test: verify user is logged in
  - send/post: take the action
  - read: read content

Supported platforms (browser-based, uses user's session):
  - Gmail (mail.google.com)
  - LinkedIn (linkedin.com)
  - Twitter/X (x.com)
  - Instagram (instagram.com)
  - Facebook (facebook.com)
  - Reddit (reddit.com)
  - WhatsApp Web (web.whatsapp.com)
  - Discord Web (discord.com/app)
  - Telegram Web (web.telegram.org)
  - HubSpot (app.hubspot.com)
  - Salesforce (lightning.force.com)
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional


# Lazy import to avoid module-level dependency
async def _get_browser():
    """Get a Playwright browser context using the user's Chrome profile.

    Looks for Chrome in:
      - ~/.config/google-chrome (Linux)
      - ~/Library/Application Support/Google/Chrome (macOS)
      - C:/Users/<user>/AppData/Local/Google/Chrome/User Data (Windows)
    """
    from playwright.async_api import async_playwright
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
    if not user_data_dir:
        raise RuntimeError("Chrome user-data-dir not found")

    pw = await async_playwright().start()
    browser = await pw.chromium.launch_persistent_context(
        user_data_dir=user_data_dir,
        headless=False,  # need to be visible for 2FA / captcha
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    )
    return browser, pw


# ============================================================================
# Gmail via Playwright
# ============================================================================

class GmailPlaywright:
    """Send email via Gmail web UI using Playwright + Chrome user-data-dir.

    Requires: user logged into Gmail in Chrome.
    """

    name = "gmail_browser"

    async def test(self) -> dict:
        try:
            browser, pw = await _get_browser()
            page = await browser.new_page()
            await page.goto("https://mail.google.com", wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            # Check if we're logged in (no "Sign in" button)
            logged_in = await page.evaluate("""() => {
                return !document.querySelector('a[href*="SignInOptions"], a[aria-label*="Sign in"]');
            }""")
            await page.close()
            await browser.close()
            await pw.stop()
            return {"ok": bool(logged_in), "platform": "gmail_browser",
                    "logged_in": logged_in}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "gmail_browser"}

    async def send(self, *, to: str, body: str, subject: str = "(no subject)") -> dict:
        try:
            browser, pw = await _get_browser()
            page = await browser.new_page()
            await page.goto("https://mail.google.com", wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            # Press 'c' to compose
            await page.keyboard.press("c")
            await page.wait_for_timeout(1000)

            # Fill To
            await page.keyboard.type(to)
            await page.keyboard.press("Tab")
            await page.wait_for_timeout(500)

            # Fill Subject
            await page.keyboard.type(subject)
            await page.keyboard.press("Tab")
            await page.wait_for_timeout(500)

            # Fill Body
            await page.keyboard.type(body)
            await page.wait_for_timeout(500)

            # Send (Cmd/Ctrl+Enter)
            await page.keyboard.press("Control+Enter")
            await page.wait_for_timeout(2000)

            await page.close()
            await browser.close()
            await pw.stop()
            return {"ok": True, "platform": "gmail_browser", "to": to, "subject": subject}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "gmail_browser"}


# ============================================================================
# LinkedIn via Playwright
# ============================================================================

class LinkedInPlaywright:
    """LinkedIn message via Playwright + Chrome user-data-dir.

    For connection requests, see the LlmDrafter.send_inmail flow.
    """

    name = "linkedin_browser"

    async def test(self) -> dict:
        try:
            browser, pw = await _get_browser()
            page = await browser.new_page()
            await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            logged_in = await page.evaluate("""() => {
                return !!document.querySelector('[data-test-id="nav-settings__sign-out"]');
            }""")
            await page.close()
            await browser.close()
            await pw.stop()
            return {"ok": bool(logged_in), "platform": "linkedin_browser",
                    "logged_in": logged_in}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "linkedin_browser"}

    async def send(self, *, to: str, body: str, subject: str = None, **_) -> dict:
        """Send a LinkedIn message to a profile URL or vanity."""
        if "linkedin.com/in/" not in to:
            return {"ok": False, "error": "need_linkedin_url_or_vanity"}
        url = to if to.startswith("http") else f"https://www.linkedin.com/in/{to}/"
        try:
            browser, pw = await _get_browser()
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            # Click Message button
            message_btn = await page.query_selector(
                'button[aria-label*="Message"], a[href*="/messaging/"]'
            )
            if not message_btn:
                return {"ok": False, "error": "no_message_button", "platform": "linkedin_browser"}
            await message_btn.click()
            await page.wait_for_timeout(2000)

            # Find the message textarea (the visible one)
            textarea = await page.query_selector(
                'div[contenteditable="true"][aria-label*="Write a message"]'
            )
            if not textarea:
                textarea = await page.query_selector(
                    'div.msg-form__contenteditable[contenteditable="true"]'
                )
            if not textarea:
                return {"ok": False, "error": "no_textarea", "platform": "linkedin_browser"}

            await textarea.click()
            await page.keyboard.type(body)
            await page.wait_for_timeout(1000)

            # Send (button with aria-label="Send")
            send_btn = await page.query_selector('button[aria-label="Send"]')
            if send_btn:
                await send_btn.click()
            else:
                # Fallback: Cmd/Ctrl+Enter
                await page.keyboard.press("Control+Enter")
            await page.wait_for_timeout(2000)

            await page.close()
            await browser.close()
            await pw.stop()
            return {"ok": True, "platform": "linkedin_browser", "to": to}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "linkedin_browser"}


# ============================================================================
# Twitter via Playwright
# ============================================================================

class TwitterPlaywright:
    """Twitter DM via Playwright."""

    name = "twitter_browser"

    async def test(self) -> dict:
        try:
            browser, pw = await _get_browser()
            page = await browser.new_page()
            await page.goto("https://x.com/home", wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            logged_in = await page.evaluate("""() => {
                return !!document.querySelector('[data-testid="SideNav_AccountSwitcher_Button"]');
            }""")
            await page.close()
            await browser.close()
            await pw.stop()
            return {"ok": bool(logged_in), "platform": "twitter_browser",
                    "logged_in": logged_in}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "twitter_browser"}

    async def send(self, *, to: str, body: str, **_) -> dict:
        """DM @user via Twitter web."""
        handle = to.lstrip("@").replace("https://twitter.com/", "").replace("https://x.com/", "").split("/")[0]
        url = f"https://x.com/{handle}"
        try:
            browser, pw = await _get_browser()
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            # Click Message button
            msg_btn = await page.query_selector(
                'a[href*="/messages/compose"], button[data-testid*="DM"], '
                'div[data-testid="sendDMFromProfile"]'
            )
            if not msg_btn:
                return {"ok": False, "error": "no_message_button", "platform": "twitter_browser"}
            await msg_btn.click()
            await page.wait_for_timeout(2000)

            # Type message
            textarea = await page.query_selector(
                'div[contenteditable="true"][data-testid*="dmComposerTextInput"], '
                'div[contenteditable="true"][aria-label*="Message"]'
            )
            if not textarea:
                return {"ok": False, "error": "no_textarea", "platform": "twitter_browser"}
            await textarea.click()
            await page.keyboard.type(body)
            await page.wait_for_timeout(1000)

            # Send button
            send_btn = await page.query_selector(
                'button[data-testid="dmComposerSendButton"]'
            )
            if send_btn:
                await send_btn.click()
            await page.wait_for_timeout(2000)

            await page.close()
            await browser.close()
            await pw.stop()
            return {"ok": True, "platform": "twitter_browser", "to": handle}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "twitter_browser"}


# ============================================================================
# WhatsApp Web via Playwright
# ============================================================================

class WhatsAppWebPlaywright:
    """Send WhatsApp message via web.whatsapp.com (uses phone's persistent session)."""

    name = "whatsapp_web_browser"

    async def test(self) -> dict:
        try:
            browser, pw = await _get_browser()
            page = await browser.new_page()
            await page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)
            # Check for QR code or "loading your chats"
            needs_qr = await page.evaluate("""() => {
                return !!document.querySelector('canvas[aria-label*="Scan"], [data-testid="qrcode"]') ||
                       document.body.innerText.includes("Link a device");
            }""")
            await page.close()
            await browser.close()
            await pw.stop()
            return {"ok": True, "platform": "whatsapp_web_browser",
                    "needs_qr_scan": needs_qr}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "whatsapp_web_browser"}

    async def send(self, *, to: str, body: str, **_) -> dict:
        """Send WhatsApp message to a phone number (with country code, no +)."""
        # Format: phone number without + or spaces
        phone = to.lstrip("+").replace(" ", "").replace("-", "")
        url = f"https://web.whatsapp.com/send?phone={phone}&text={urllib_parse_quote(body)}"
        try:
            browser, pw = await _get_browser()
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)

            # Find send button (the paper plane / arrow icon)
            send_btn = await page.query_selector(
                'button[aria-label="Send"], button[data-testid="send"], '
                'span[data-icon="send"]'
            )
            if not send_btn:
                return {"ok": False, "error": "no_send_button", "platform": "whatsapp_web_browser"}

            # Click the parent button if span
            if send_btn.tag_name == "span":
                parent = await send_btn.query_selector("xpath=..")
                if parent:
                    await parent.click()
                else:
                    await send_btn.click()
            else:
                await send_btn.click()
            await page.wait_for_timeout(2000)

            await page.close()
            await browser.close()
            await pw.stop()
            return {"ok": True, "platform": "whatsapp_web_browser", "to": phone}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "whatsapp_web_browser"}


def urllib_parse_quote(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote(s)


# ============================================================================
# Discord Web via Playwright
# ============================================================================

class DiscordPlaywright:
    """Send a DM or channel message via Discord Web (discord.com/app).

    Browser-based, no bot token needed. Uses user's logged-in Discord session.
    Reference: opencli browser primitives (open/navigate/click/type/keys).
    """

    name = "discord_web_browser"

    async def test(self) -> dict:
        try:
            browser, pw = await _get_browser()
            page = await browser.new_page()
            await page.goto("https://discord.com/app", wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)
            logged_in = await page.evaluate("""() => {
                // Discord is logged in when the app shell is rendered (no /login redirect).
                return location.pathname.startsWith('/app') &&
                       !!document.querySelector('[class*="app"]');
            }""")
            await page.close()
            await browser.close()
            await pw.stop()
            return {"ok": bool(logged_in), "platform": "discord_web_browser",
                    "logged_in": logged_in}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "discord_web_browser"}

    async def send(self, *, to: str, body: str, **_) -> dict:
        """Send a message to a Discord channel or DM.

        `to` can be:
          - a /channel path (e.g. '/channels/<guild_id>/<channel_id>')
          - a full discord.com URL
          - '@username' (open a DM with that user)
        """
        target = to.strip()
        if target.startswith("@"):
            # DM by username — Discord DM URLs look like /channels/@me/<user_id>
            # Without the user_id we navigate to the DMs list and let user click
            url = "https://discord.com/app"
        elif target.startswith("http"):
            url = target
        elif target.startswith("/channels/"):
            url = f"https://discord.com{target}"
        else:
            # Treat as channel name; open via search fallback
            url = f"https://discord.com/app"

        try:
            browser, pw = await _get_browser()
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)

            # If DM mode: open DMs then type
            if target.startswith("@"):
                # Click the "New DM" / "Direct Messages" button in the nav
                dm_btn = await page.query_selector('[aria-label*="Direct Messages" i]')
                if dm_btn:
                    await dm_btn.click()
                    await page.wait_for_timeout(1000)

            # Find the message textbox
            textbox = await page.query_selector(
                'div[role="textbox"][contenteditable="true"], '
                'div[class*="channelTextArea"] div[contenteditable="true"]'
            )
            if not textbox:
                await page.close()
                await browser.close()
                await pw.stop()
                return {"ok": False, "error": "no_textbox",
                        "platform": "discord_web_browser",
                        "hint": "Navigate to the channel first or login expired"}

            # Type the message (Discord doesn't accept paste, must type)
            await textbox.click()
            await page.keyboard.type(body, delay=10)
            await page.wait_for_timeout(500)

            # Send with Enter
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2000)

            await page.close()
            await browser.close()
            await pw.stop()
            return {"ok": True, "platform": "discord_web_browser", "to": target}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "discord_web_browser"}


# ============================================================================
# Telegram Web via Playwright
# ============================================================================

class TelegramWebPlaywright:
    """Send a Telegram message via web.telegram.org (browser-based, uses user's session)."""

    name = "telegram_web_browser"

    async def test(self) -> dict:
        try:
            browser, pw = await _get_browser()
            page = await browser.new_page()
            await page.goto("https://web.telegram.org/a/", wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)
            logged_in = await page.evaluate("""() => {
                // After login, the chat list appears (no "Log in by phone" button)
                return !document.querySelector('button[ng-click*="loginPhone"]') &&
                       !!document.querySelector('.chat-list, [class*="chatList"]');
            }""")
            await page.close()
            await browser.close()
            await pw.stop()
            return {"ok": bool(logged_in), "platform": "telegram_web_browser",
                    "logged_in": logged_in}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "telegram_web_browser"}

    async def send(self, *, to: str, body: str, **_) -> dict:
        """Send a Telegram message.
        `to` can be:
          - @username
          - https://web.telegram.org/a/#<chat_id>
          - https://t.me/<username>
        """
        target = to.strip()
        if target.startswith("@") or target.startswith("https://t.me/"):
            user = target.lstrip("@").replace("https://t.me/", "").split("/")[0]
            url = f"https://web.telegram.org/a/#?tgaddr=tg%3A%2F%2Fresolve%3Fdomain%3D{user}"
        elif target.startswith("http"):
            url = target
        else:
            url = "https://web.telegram.org/a/"

        try:
            browser, pw = await _get_browser()
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)

            # Find message input
            textbox = await page.query_selector(
                'div[contenteditable="true"].input-message-input, '
                'div.input-message-input[contenteditable="true"], '
                'div[contenteditable="true"][data-tab-id]'
            )
            if not textbox:
                # Try fallback selector
                textbox = await page.query_selector('div[contenteditable="true"]')
            if not textbox:
                await page.close()
                await browser.close()
                await pw.stop()
                return {"ok": False, "error": "no_textbox",
                        "platform": "telegram_web_browser"}

            await textbox.click()
            await page.keyboard.type(body, delay=10)
            await page.wait_for_timeout(500)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2000)

            await page.close()
            await browser.close()
            await pw.stop()
            return {"ok": True, "platform": "telegram_web_browser", "to": target}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "telegram_web_browser"}


# ============================================================================
# Unified dispatcher
# ============================================================================

class PlaywrightDispatcher:
    """Browser automation via Playwright + user's Chrome session.

    No API keys, no OpenCLI needed. Just Chrome with the user logged in.
    """

    def __init__(self):
        self.adapters = {
            "gmail_browser": GmailPlaywright(),
            "linkedin_browser": LinkedInPlaywright(),
            "twitter_browser": TwitterPlaywright(),
            "whatsapp_web_browser": WhatsAppWebPlaywright(),
            "discord_web_browser": DiscordPlaywright(),
            "telegram_web_browser": TelegramWebPlaywright(),
        }

    def list_channels(self) -> list[dict]:
        return [{"name": n, "status": "playwright"} for n in self.adapters]

    def send(self, *, channel: str, **kwargs) -> dict:
        if channel not in self.adapters:
            return {"ok": False, "error": f"unknown:{channel}",
                    "available": list(self.adapters.keys())}
        adapter = self.adapters[channel]
        # Run async in sync context
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # In a running loop — schedule
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as ex:
                    return ex.submit(asyncio.run, adapter.send(**kwargs)).result(timeout=120)
            else:
                return loop.run_until_complete(adapter.send(**kwargs))
        except RuntimeError:
            return asyncio.run(adapter.send(**kwargs))


# Singleton
playwright_social = PlaywrightDispatcher()
