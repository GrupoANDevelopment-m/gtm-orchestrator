"""
Outbound channel adapters.

Each adapter has:
  - send(*, to, body, subject=None, metadata=None) -> dict
  - status: "live" (real API key) | "dry-run" (no key, logs the send)

Adapters:
  - TwilioAdapter: WhatsApp + SMS via Twilio
  - SendGridAdapter: Email via SendGrid
  - ApolloAdapter: Contact/company enrichment via Apollo.io
  - HunterAdapter: Email finding via Hunter.io
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.parse
from typing import Any


def _env(name: str) -> str | None:
    v = os.environ.get(name)
    return v.strip() if v else None


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


# ============================================================================
# TWILIO — WhatsApp + SMS
# ============================================================================
class TwilioAdapter:
    """Send WhatsApp or SMS via Twilio. Falls back to dry-run if no credentials."""

    def __init__(self):
        self.account_sid = _env("TWILIO_ACCOUNT_SID") or _key_from_file(".twilio_account_sid.txt")
        self.auth_token = _env("TWILIO_AUTH_TOKEN") or _key_from_file(".twilio_auth_token.txt")
        self.from_number = _env("TWILIO_FROM") or _key_from_file(".twilio_from.txt") or "+15005550006"
        self.from_whatsapp = _env("TWILIO_WHATSAPP_FROM") or _key_from_file(".twilio_whatsapp.txt") or "whatsapp:+14155238886"

    @property
    def status(self) -> str:
        return "live" if (self.account_sid and self.auth_token) else "dry-run"

    def send(self, *, to: str, body: str, channel: str = "whatsapp", metadata: dict | None = None) -> dict:
        """Send a message. channel: 'whatsapp' or 'sms'."""
        if self.status == "dry-run":
            return {
                "ok": True,
                "channel": channel,
                "to": to,
                "from": self.from_whatsapp if channel == "whatsapp" else self.from_number,
                "body_preview": body[:120],
                "mode": "dry-run",
                "metadata": metadata or {},
            }
        # Live Twilio API
        from_number = self.from_whatsapp if channel == "whatsapp" else self.from_number
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        # Basic auth
        import base64
        creds = base64.b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode()
        form = urllib.parse.urlencode({
            "From": from_number,
            "To": to,
            "Body": body,
        }).encode()
        req = urllib.request.Request(
            url,
            data=form,
            headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode())
            return {
                "ok": True,
                "channel": channel,
                "to": to,
                "from": from_number,
                "sid": data.get("sid"),
                "status": data.get("status"),
                "mode": "live",
            }
        except Exception as e:
            return {"ok": False, "channel": channel, "to": to, "error": str(e)}


# ============================================================================
# SENDGRID — Email
# ============================================================================
class SendGridAdapter:
    """Send transactional email via SendGrid."""

    def __init__(self):
        self.api_key = _env("SENDGRID_API_KEY") or _key_from_file(".sendgrid_api_key.txt")
        self.from_email = _env("SENDGRID_FROM") or _key_from_file(".sendgrid_from.txt") or "noreply@orchestrator.local"

    @property
    def status(self) -> str:
        return "live" if self.api_key else "dry-run"

    def send(self, *, to: str, subject: str, body: str, channel: str = "email", metadata: dict | None = None) -> dict:
        if self.status == "dry-run":
            return {
                "ok": True,
                "channel": "email",
                "to": to,
                "from": self.from_email,
                "subject": subject,
                "body_preview": body[:120],
                "mode": "dry-run",
                "metadata": metadata or {},
            }
        # Live SendGrid
        url = "https://api.sendgrid.com/v3/mail/send"
        body_json = {
            "personalizations": [{"to": [{"email": to}], "subject": subject}],
            "from": {"email": self.from_email},
            "content": [{"type": "text/plain", "value": body}],
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body_json).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                # SendGrid returns 202 with no body
                return {"ok": True, "to": to, "subject": subject, "mode": "live", "status_code": r.status}
        except urllib.error.HTTPError as e:
            return {"ok": False, "to": to, "error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
        except Exception as e:
            return {"ok": False, "to": to, "error": str(e)}


# ============================================================================
# APOLLO — Contact/company enrichment
# ============================================================================
class ApolloAdapter:
    """Enrich a person or company via Apollo.io."""

    def __init__(self):
        self.api_key = _env("APOLLO_API_KEY") or _key_from_file(".apollo_api_key.txt")

    @property
    def status(self) -> str:
        return "live" if self.api_key else "dry-run"

    def enrich_person(self, *, email: str = None, name: str = None, domain: str = None) -> dict:
        if self.status == "dry-run":
            return {"ok": True, "mode": "dry-run", "person": None, "note": "APOLLO_API_KEY not set"}
        url = "https://api.apollo.io/v1/people/match"
        body = {"api_key": self.api_key}
        if email: body["email"] = email
        if name: body["name"] = name
        if domain: body["domain"] = domain
        try:
            return _http_post(url, {}, body)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def enrich_company(self, *, domain: str) -> dict:
        if self.status == "dry-run":
            return {"ok": True, "mode": "dry-run", "company": None, "note": "APOLLO_API_KEY not set"}
        url = "https://api.apollo.io/v1/organizations/enrich"
        body = {"api_key": self.api_key, "domain": domain}
        try:
            return _http_post(url, {}, body)
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ============================================================================
# HUNTER — Email finding
# ============================================================================
class HunterAdapter:
    """Find a person's email via Hunter.io."""

    def __init__(self):
        self.api_key = _env("HUNTER_API_KEY") or _key_from_file(".hunter_api_key.txt")

    @property
    def status(self) -> str:
        return "live" if self.api_key else "dry-run"

    def find_email(self, *, domain: str, first_name: str, last_name: str) -> dict:
        if self.status == "dry-run":
            return {"ok": True, "mode": "dry-run", "email": None, "note": "HUNTER_API_KEY not set"}
        url = "https://api.hunter.io/v2/email-finder"
        params = {"domain": domain, "first_name": first_name, "last_name": last_name, "api_key": self.api_key}
        url = url + "?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            return {"ok": False, "error": str(e)}


# Singleton instances (cheap to construct)
twilio = TwilioAdapter()
sendgrid = SendGridAdapter()
apollo = ApolloAdapter()
hunter = HunterAdapter()
