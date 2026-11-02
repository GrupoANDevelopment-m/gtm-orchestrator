"""
Multi-platform social/messaging adapters.

READ-side (research/discovery):
  - AgentReach: 15 channels via /workspace/orchestrator/upstream/agent-reach
    (Twitter, LinkedIn, Instagram, Facebook, Reddit, GitHub, YouTube, etc.)
  - All read-only, use public APIs or OpenCLI + Chrome login

WRITE-side (outreach/messaging):
  - WhatsAppAdapter:  WhatsApp Business Cloud API (Meta)
                      https://developers.facebook.com/docs/whatsapp/cloud-api
  - DiscordAdapter:   Webhook + Bot Token
                      https://discord.com/developers/docs/intro
  - TelegramAdapter:  Bot API
                      https://core.telegram.org/bots/api
  - LinkedInAdapter:  Marketing Developer Platform (write requires partner status)
                      Falls back to read-only via agent-reach
  - InstagramAdapter: Graph API (write requires business account)
                      Falls back to read-only via agent-reach

All adapters have:
  - send(*, to, body, subject=None, metadata=None) -> dict
  - status: "live" (real API key) | "dry-run" (no key, logs the send)
  - test() -> bool
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.parse
from typing import Any, Optional


def _key_from_file(filename: str) -> str | None:
    keys_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "keys")
    p = os.path.join(keys_dir, filename)
    if os.path.exists(p):
        return open(p).read().strip()
    return None


def _http_post(url: str, headers: dict, body: dict, *, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _http_get(url: str, headers: dict, *, timeout: int = 30) -> dict | str:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ct = r.headers.get("content-type", "")
        if "json" in ct:
            return json.loads(r.read().decode())
        return r.read().decode()


# ============================================================================
# WhatsApp Business Cloud API (Meta)
# ============================================================================

class WhatsAppAdapter:
    """WhatsApp Business Cloud API — Meta Graph v18+.

    Auth:   Bearer <WHATSAPP_TOKEN>
    URL:    https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages
    Setup:  https://developers.facebook.com/docs/whatsapp/cloud-api/get-started

    Templates: pre-approved message templates for outbound (free-form only
               works inside 24h customer-service window).
    """

    name = "whatsapp"

    def __init__(self):
        self.token = _key_from_file(".whatsapp_token.txt") or os.environ.get("WHATSAPP_TOKEN", "").strip()
        self.phone_id = _key_from_file(".whatsapp_phone_id.txt") or os.environ.get("WHATSAPP_PHONE_ID", "").strip()
        self.business_id = _key_from_file(".whatsapp_business_id.txt") or os.environ.get("WHATSAPP_BUSINESS_ID", "").strip()

    @property
    def status(self) -> str:
        return "live" if (self.token and self.phone_id) else "dry-run"

    def test(self) -> dict:
        """Verify token + fetch phone number metadata."""
        if self.status != "live":
            return {"ok": False, "error": "no_credentials",
                    "hint": "Set WHATSAPP_TOKEN + WHATSAPP_PHONE_ID or save to keys/.whatsapp_*.txt"}
        try:
            r = _http_get(
                f"https://graph.facebook.com/v18.0/{self.phone_id}",
                {"Authorization": f"Bearer {self.token}"}
            )
            return {"ok": True, "phone": r.get("display_phone_number"),
                    "verified_name": r.get("verified_name"),
                    "platform": "whatsapp"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def send(self, *, to: str, body: str, template: Optional[str] = None,
             template_vars: Optional[list] = None,
             subject: Optional[str] = None,
             metadata: Optional[dict] = None,
             transport: Optional[str] = None, **_) -> dict:
        """Send WhatsApp text or template message.

        Args:
          to: E.164 phone number, e.g. "+15551234567"
          body: text body (used for plain text or template parameter)
          template: optional pre-approved template name (required for cold outreach)
          template_vars: list of template variables
          transport: 'api' (default) or 'browser'. When 'browser', falls back
                     to WhatsApp Web via Playwright + user's Chrome session.
                     This is the free path (no Meta Business API needed).
        """
        # Browser-fallback path — uses the user's logged-in WhatsApp Web session.
        # No Meta API key required. Requires Chrome with WhatsApp Web linked.
        if transport == "browser" or (self.status != "live" and
                                       (transport or "").lower() == "browser"):
            try:
                from playwright_social import playwright_social
                r = playwright_social.send(channel="whatsapp_web_browser",
                                           to=to, body=body)
                r["transport"] = "browser"
                r["platform"] = "whatsapp"
                return r
            except Exception as e:
                return {"ok": False, "error": f"browser_fallback_failed:{e}",
                        "platform": "whatsapp", "transport": "browser"}

        if self.status != "live":
            return {"ok": True, "dry_run": True, "platform": "whatsapp",
                    "would_send": {"to": to, "body": body, "template": template,
                                   "template_vars": template_vars,
                                   "subject": subject,
                                   "metadata": metadata},
                    "hint": "Add transport=browser to use WhatsApp Web via your Chrome session (no API key needed)"}

        url = f"https://graph.facebook.com/v18.0/{self.phone_id}/messages"
        headers = {"Authorization": f"Bearer {self.token}"}

        if template:
            components = [{
                "type": "body",
                "parameters": [{"type": "text", "text": v} for v in (template_vars or [body])]
            }]
            payload = {
                "messaging_product": "whatsapp",
                "to": to.lstrip("+"),
                "type": "template",
                "template": {
                    "name": template,
                    "language": {"code": "en_US"},
                    "components": components,
                },
            }
        else:
            payload = {
                "messaging_product": "whatsapp",
                "to": to.lstrip("+"),
                "type": "text",
                "text": {"body": body, "preview_url": False},
            }

        try:
            r = _http_post(url, headers, payload)
            return {"ok": True, "platform": "whatsapp", "result": r,
                    "message_id": r.get("messages", [{}])[0].get("id"),
                    "metadata": metadata}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "whatsapp"}


# ============================================================================
# Discord Webhook + Bot
# ============================================================================

class DiscordAdapter:
    """Discord Webhook (simple) + Bot Token (rich).

    Webhook: one-way, no auth, just POST to a URL — perfect for notifications.
    Bot:     send to channel via Bot Token + channel_id — two-way capable.
    """

    name = "discord"

    def __init__(self):
        self.webhook_url = _key_from_file(".discord_webhook.txt") or os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
        self.bot_token = _key_from_file(".discord_bot_token.txt") or os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        self.default_channel = _key_from_file(".discord_channel_id.txt") or os.environ.get("DISCORD_CHANNEL_ID", "").strip()

    @property
    def status(self) -> str:
        if self.webhook_url or self.bot_token:
            return "live"
        return "dry-run"

    def test(self) -> dict:
        if self.webhook_url:
            # Webhook exists; verify by sending a test ping
            try:
                _http_post(self.webhook_url, {}, {"content": "orchestrator test ping"})
                return {"ok": True, "mode": "webhook", "platform": "discord"}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        elif self.bot_token and self.default_channel:
            try:
                r = _http_get(
                    f"https://discord.com/api/v10/channels/{self.default_channel}",
                    {"Authorization": f"Bot {self.bot_token}"}
                )
                return {"ok": True, "mode": "bot", "channel": r.get("name"),
                        "platform": "discord"}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        return {"ok": False, "error": "no_credentials",
                "hint": "Set DISCORD_WEBHOOK_URL or DISCORD_BOT_TOKEN+CHANNEL_ID"}

    def send(self, *, to: str, body: str, subject: Optional[str] = None,
             metadata: Optional[dict] = None,
             transport: Optional[str] = None, **_) -> dict:
        """Send Discord message.

        Args:
          to: channel_id (bot mode) or "webhook" (webhook mode), or
              '/channels/<guild>/<channel>' (browser mode), or '@user' (DM).
          body: message text
          subject: optional embed title
          transport: 'api' (default) or 'browser'. When 'browser', uses
                     Discord Web via Playwright + user's logged-in session.
                     No bot token required.
        """
        # Browser-fallback path
        if transport == "browser" or (self.status == "dry-run" and
                                       (transport or "").lower() == "browser"):
            try:
                from playwright_social import playwright_social
                # If `to` is a channel id, convert to /channels/<id>
                target = to
                if to.isdigit():
                    target = f"/channels/@me/{to}"
                r = playwright_social.send(channel="discord_web_browser",
                                           to=target, body=body)
                r["transport"] = "browser"
                r["platform"] = "discord"
                return r
            except Exception as e:
                return {"ok": False, "error": f"browser_fallback_failed:{e}",
                        "platform": "discord", "transport": "browser"}

        if self.status == "dry-run":
            return {"ok": True, "dry_run": True, "platform": "discord",
                    "would_send": {"to": to, "body": body, "subject": subject,
                                   "metadata": metadata},
                    "hint": "Add transport=browser to use Discord Web via your Chrome session (no bot token needed)"}

        # Webhook mode
        if self.webhook_url and (to == "webhook" or not self.bot_token):
            payload = {"content": body}
            if subject:
                payload = {"embeds": [{"title": subject, "description": body, "color": 5814783}]}
            try:
                _http_post(self.webhook_url, {}, payload)
                return {"ok": True, "platform": "discord", "mode": "webhook",
                        "metadata": metadata}
            except Exception as e:
                return {"ok": False, "error": str(e), "platform": "discord"}

        # Bot mode
        if self.bot_token:
            channel_id = to if to.startswith(("1", "2", "3", "4", "5", "6", "7", "8", "9")) else self.default_channel
            url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
            headers = {"Authorization": f"Bot {self.bot_token}"}
            payload = {"content": body}
            if subject:
                payload = {"embeds": [{"title": subject, "description": body, "color": 5814783}]}
            try:
                r = _http_post(url, headers, payload)
                return {"ok": True, "platform": "discord", "mode": "bot",
                        "message_id": r.get("id"), "channel_id": r.get("channel_id"),
                        "metadata": metadata}
            except Exception as e:
                return {"ok": False, "error": str(e), "platform": "discord"}

        return {"ok": False, "error": "no_send_path", "platform": "discord"}


# ============================================================================
# Telegram Bot API
# ============================================================================

class TelegramAdapter:
    """Telegram Bot API.

    Setup:  https://t.me/BotFather  →  /newbot  →  save token
    Send:   https://api.telegram.org/bot<TOKEN>/sendMessage
    """

    name = "telegram"

    def __init__(self):
        self.token = _key_from_file(".telegram_bot_token.txt") or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.default_chat = _key_from_file(".telegram_chat_id.txt") or os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    @property
    def status(self) -> str:
        return "live" if self.token else "dry-run"

    def test(self) -> dict:
        if self.status != "live":
            return {"ok": False, "error": "no_credentials",
                    "hint": "Set TELEGRAM_BOT_TOKEN or save to keys/.telegram_bot_token.txt",
                    "platform": "telegram"}
        try:
            r = _http_get(
                f"https://api.telegram.org/bot{self.token}/getMe",
                headers={}
            )
            if r.get("ok"):
                return {"ok": True, "bot": r["result"].get("username"),
                        "id": r["result"].get("id"), "platform": "telegram"}
            return {"ok": False, "error": r, "platform": "telegram"}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "telegram"}

    def send(self, *, to: str, body: str, subject: Optional[str] = None,
             parse_mode: Optional[str] = "Markdown",
             metadata: Optional[dict] = None,
             transport: Optional[str] = None, **_) -> dict:
        """Send Telegram message.

        Args:
          to: chat_id (numeric) or @channelusername
          body: message text (Markdown/HTML if parse_mode set)
          subject: optional header line prepended
          transport: 'api' (default) or 'browser'. Browser mode uses
                     Telegram Web via Playwright (no bot token needed).
        """
        # Browser-fallback path
        if transport == "browser" or (self.status == "dry-run" and
                                       (transport or "").lower() == "browser"):
            try:
                from playwright_social import playwright_social
                r = playwright_social.send(channel="telegram_web_browser",
                                           to=to, body=body)
                r["transport"] = "browser"
                r["platform"] = "telegram"
                return r
            except Exception as e:
                return {"ok": False, "error": f"browser_fallback_failed:{e}",
                        "platform": "telegram", "transport": "browser"}

        if self.status == "dry-run":
            return {"ok": True, "dry_run": True, "platform": "telegram",
                    "would_send": {"to": to, "body": body, "subject": subject,
                                   "metadata": metadata},
                    "hint": "Add transport=browser to use Telegram Web via your Chrome session (no bot token needed)"}

        text = f"*{subject}*\n\n{body}" if subject else body
        chat_id = to if to else self.default_chat
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text[:4096]}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            r = _http_post(url, {}, payload)
            if r.get("ok"):
                return {"ok": True, "platform": "telegram",
                        "message_id": r["result"].get("message_id"),
                        "chat_id": r["result"].get("chat", {}).get("id"),
                        "metadata": metadata}
            return {"ok": False, "error": r, "platform": "telegram"}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "telegram"}


# ============================================================================
# LinkedIn — read-only via agent-reach, write is partner-only
# ============================================================================

class LinkedInAdapter:
    """LinkedIn messaging — write requires Sales Navigator API or partner status.

    Read-side: delegate to agent-reach (LinkedInChannel).
    Write-side: dry-run only unless LINKEDIN_ACCESS_TOKEN is set
                (Marketing Developer Platform / Sales Navigator API).
    """

    name = "linkedin"

    def __init__(self):
        self.access_token = _key_from_file(".linkedin_access_token.txt") or os.environ.get("LINKEDIN_ACCESS_TOKEN", "").strip()
        self.person_urn = _key_from_file(".linkedin_person_urn.txt") or os.environ.get("LINKEDIN_PERSON_URN", "").strip()

    @property
    def status(self) -> str:
        return "live" if self.access_token else "read-only"

    def test(self) -> dict:
        if self.status != "live":
            return {"ok": False, "error": "no_credentials",
                    "hint": "Read-only via agent-reach. Write requires Marketing Developer Platform access.",
                    "platform": "linkedin"}
        try:
            r = _http_get(
                "https://api.linkedin.com/v2/me",
                {"Authorization": f"Bearer {self.access_token}",
                 "X-Restli-Protocol-Version": "2.0.0"}
            )
            return {"ok": True, "platform": "linkedin", "id": r.get("id")}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "linkedin"}

    def send(self, *, to: str, body: str, subject: Optional[str] = None,
             metadata: Optional[dict] = None, **_) -> dict:
        """Send LinkedIn message via Marketing Developer Platform.

        Requires:
          - LinkedIn Marketing Developer Platform access
          - w_member_social permission
          - recipient profile URN
        """
        if self.status != "live":
            return {"ok": True, "dry_run": True, "platform": "linkedin",
                    "hint": "Read-only via agent-reach. Write requires Marketing Developer Platform access.",
                    "would_send": {"to": to, "body": body, "metadata": metadata}}

        # Real impl: POST https://api.linkedin.com/v2/messages
        url = "https://api.linkedin.com/v2/messages"
        headers = {"Authorization": f"Bearer {self.access_token}",
                   "X-Restli-Protocol-Version": "2.0.0"}
        payload = {
            "recipients": [to],  # List of profile URNs
            "message": body[:1300],  # LinkedIn message limit
            "subject": subject or "",
        }
        try:
            _http_post(url, headers, payload)
            return {"ok": True, "platform": "linkedin", "metadata": metadata}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "linkedin"}


# ============================================================================
# Instagram — read via agent-reach, write via Graph API
# ============================================================================

class InstagramAdapter:
    """Instagram DMs via Meta Graph API.

    Setup:  https://developers.facebook.com/docs/instagram-api
    Requires:
      - Instagram Business or Creator account
      - Facebook Page connected to the Instagram account
      - pages_messaging permission
    """

    name = "instagram"

    def __init__(self):
        self.access_token = _key_from_file(".instagram_access_token.txt") or os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
        self.page_id = _key_from_file(".instagram_page_id.txt") or os.environ.get("INSTAGRAM_PAGE_ID", "").strip()

    @property
    def status(self) -> str:
        return "live" if (self.access_token and self.page_id) else "read-only"

    def test(self) -> dict:
        if self.status != "live":
            return {"ok": False, "error": "no_credentials",
                    "hint": "Read via agent-reach. Write requires Instagram Business + Page + pages_messaging.",
                    "platform": "instagram"}
        try:
            r = _http_get(
                f"https://graph.facebook.com/v18.0/{self.page_id}",
                {"Authorization": f"Bearer {self.access_token}"}
            )
            return {"ok": True, "platform": "instagram", "page": r.get("name")}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "instagram"}

    def send(self, *, to: str, body: str, metadata: Optional[dict] = None, **_) -> dict:
        """Send Instagram DM.

        Args:
          to: Instagram-Scoped ID (IGSID) of recipient
        """
        if self.status != "live":
            return {"ok": True, "dry_run": True, "platform": "instagram",
                    "hint": "Read via agent-reach. Write requires Instagram Business + Page + pages_messaging.",
                    "would_send": {"to": to, "body": body, "metadata": metadata}}

        # Real impl: POST /{page-id}/messages
        url = f"https://graph.facebook.com/v18.0/{self.page_id}/messages"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        payload = {
            "recipient": {"id": to},
            "message": {"text": body[:1000]},  # IG message limit
            "messaging_type": "MESSAGE_TAG",
            "tag": "ACCOUNT_UPDATE",  # or HUMAN_AGENT, etc.
        }
        try:
            r = _http_post(url, headers, payload)
            return {"ok": True, "platform": "instagram", "result": r, "metadata": metadata}
        except Exception as e:
            return {"ok": False, "error": str(e), "platform": "instagram"}


# ============================================================================
# Unified Social Dispatcher
# ============================================================================

class SocialDispatcher:
    """Pick the right channel for outreach based on lead context.

    Auto-routing rules (priority order):
      1. Explicit "preferred_channel" in metadata
      2. If lead has phone → WhatsApp (if live) or SMS (Twilio)
      3. If lead has email → Email (SendGrid)
      4. If lead has telegram_id → Telegram
      5. If lead has discord_id → Discord DM
      6. If lead has linkedin_url → LinkedIn
      7. If lead has instagram_handle → Instagram
      8. Default: best available live channel
    """

    def __init__(self):
        self.adapters = {
            "whatsapp": WhatsAppAdapter(),
            "discord": DiscordAdapter(),
            "telegram": TelegramAdapter(),
            "linkedin": LinkedInAdapter(),
            "instagram": InstagramAdapter(),
        }

    def list_channels(self) -> list[dict]:
        return [{"name": n, "status": a.status} for n, a in self.adapters.items()]

    def send(self, *, channel: str, to: str, body: str,
             subject: Optional[str] = None,
             metadata: Optional[dict] = None, **kwargs) -> dict:
        """Send via specific channel."""
        if channel not in self.adapters:
            return {"ok": False, "error": f"unknown_channel:{channel}",
                    "available": list(self.adapters.keys())}
        return self.adapters[channel].send(
            to=to, body=body, subject=subject, metadata=metadata, **kwargs
        )

    def auto_send(self, *, lead: dict, body: str, subject: Optional[str] = None,
                  preferred: Optional[str] = None) -> dict:
        """Auto-pick channel and send.

        Args:
          lead: dict with at least one of:
                phone, email, telegram_id, discord_id,
                linkedin_url, instagram_handle
        """
        metadata = {"lead_id": lead.get("id"), "name": lead.get("name")}

        # 1. Explicit preference
        if preferred and preferred in self.adapters:
            target = self._extract_target(preferred, lead)
            if target:
                return self.adapters[preferred].send(
                    to=target, body=body, subject=subject, metadata=metadata
                )

        # 2. WhatsApp (if phone + live)
        if lead.get("phone") and self.adapters["whatsapp"].status == "live":
            return self.adapters["whatsapp"].send(
                to=lead["phone"], body=body, subject=subject, metadata=metadata
            )

        # 3. Telegram
        if lead.get("telegram_id") and self.adapters["telegram"].status == "live":
            return self.adapters["telegram"].send(
                to=lead["telegram_id"], body=body, subject=subject, metadata=metadata
            )

        # 4. Discord
        if lead.get("discord_id") and self.adapters["discord"].status == "live":
            return self.adapters["discord"].send(
                to=lead["discord_id"], body=body, subject=subject, metadata=metadata
            )

        # 5. LinkedIn
        if lead.get("linkedin_url") and self.adapters["linkedin"].status == "live":
            return self.adapters["linkedin"].send(
                to=lead.get("linkedin_urn", lead["linkedin_url"]),
                body=body, subject=subject, metadata=metadata
            )

        # 6. Instagram
        if lead.get("instagram_handle") and self.adapters["instagram"].status == "live":
            return self.adapters["instagram"].send(
                to=lead.get("instagram_igsid", lead["instagram_handle"]),
                body=body, metadata=metadata
            )

        # 7. No live channel — return hint to use email
        return {"ok": False, "error": "no_live_channel_for_lead",
                "hint": "Provide phone/telegram_id/discord_id/linkedin_url/instagram_handle AND matching API keys",
                "lead_channels": {k: lead.get(k) for k in
                                  ("phone", "telegram_id", "discord_id",
                                   "linkedin_url", "instagram_handle")
                                  if lead.get(k)}}

    @staticmethod
    def _extract_target(channel: str, lead: dict) -> Optional[str]:
        map_ = {
            "whatsapp": "phone",
            "telegram": "telegram_id",
            "discord": "discord_id",
            "linkedin": "linkedin_urn",
            "instagram": "instagram_igsid",
        }
        return lead.get(map_.get(channel, ""))


# Singleton
social = SocialDispatcher()
