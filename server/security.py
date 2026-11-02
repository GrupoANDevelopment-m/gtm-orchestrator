"""
Production security — HMAC webhook signatures, OAuth scaffolding, Vault
abstraction, rate limiting per org.

These are the missing production-grade pieces. Each is a small, focused
implementation that maps directly to a critical security requirement.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


# === HMAC webhook signature verification ===

class WebhookSigner:
    """HMAC-SHA256 signing/verification for inbound webhooks.

    Pattern: each provider (Twilio, Mailgun, Stripe) signs requests with
    a shared secret. We verify on inbound to reject spoofed/forged events.
    """

    def __init__(self, secret: str | None = None):
        self.secret = secret or self._load_secret()

    def _load_secret(self) -> str:
        # Default to orchestrator API key if no webhook secret configured
        p = Path("/workspace/orchestrator/keys/.webhook_signing_key.txt")
        if p.exists():
            return p.read_text().strip()
        p = Path("/workspace/orchestrator/keys/.orchestrator_api_key.txt")
        if p.exists():
            return p.read_text().strip()
        return "default-dev-secret-rotate-in-prod"

    def sign(self, payload: bytes, timestamp: int | None = None) -> str:
        ts = timestamp or int(time.time())
        msg = f"{ts}.".encode() + payload
        sig = hmac.new(self.secret.encode(), msg, hashlib.sha256).hexdigest()
        return f"t={ts},v1={sig}"

    def verify(self, payload: bytes, signature_header: str,
               tolerance_seconds: int = 300) -> dict:
        """Verify a webhook signature. Returns {valid, reason}."""
        if not signature_header:
            return {"valid": False, "reason": "missing signature"}
        try:
            parts = dict(p.split("=", 1) for p in signature_header.split(","))
            ts = int(parts["t"])
            sig = parts["v1"]
        except Exception:
            return {"valid": False, "reason": "malformed signature"}
        # Tolerance check (anti-replay)
        if abs(time.time() - ts) > tolerance_seconds:
            return {"valid": False, "reason": "timestamp out of tolerance"}
        msg = f"{ts}.".encode() + payload
        expected = hmac.new(self.secret.encode(), msg, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return {"valid": False, "reason": "signature mismatch"}
        return {"valid": True, "reason": "ok", "timestamp": ts}


# === Idempotency ===

class IdempotencyTable:
    """Prevent duplicate processing of the same webhook delivery.

    Stores {request_id -> result} for a TTL window. If a delivery retries
    with the same id, we return the cached result.
    """

    def __init__(self, *, ttl_seconds: int = 86400, max_size: int = 10_000):
        self.ttl = ttl_seconds
        self.max_size = max_size
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, request_id: str) -> Any | None:
        entry = self._store.get(request_id)
        if not entry:
            return None
        ts, value = entry
        if time.time() - ts > self.ttl:
            self._store.pop(request_id, None)
            return None
        return value

    def put(self, request_id: str, value: Any) -> None:
        if len(self._store) >= self.max_size:
            # Evict oldest
            oldest = min(self._store.items(), key=lambda kv: kv[1][0])
            self._store.pop(oldest[0], None)
        self._store[request_id] = (time.time(), value)

    def stats(self) -> dict:
        return {"size": len(self._store), "ttl_seconds": self.ttl}


_idempotency = IdempotencyTable()


def get_idempotency() -> IdempotencyTable:
    return _idempotency


# === Per-org rate limit + token budget ===

class RateLimiter:
    """Token bucket per (org_id, action). Each call decrements the budget.
    Refills at the configured rate. Returns ok/retry_after."""

    def __init__(self):
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}
        # Token budget per (org, action) per 24h (in seconds)
        self._daily_budget: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=10_000))

    def check(self, org_id: str, action: str, *, max_per_minute: int = 60) -> dict:
        key = (org_id, action)
        now = time.time()
        bucket = self._buckets.get(key)
        if not bucket or now - bucket[1] > 60:
            self._buckets[key] = (max_per_minute, now)
            bucket = self._buckets[key]
        tokens, last_refill = bucket
        # Refill
        elapsed = now - last_refill
        refill = (elapsed / 60) * max_per_minute
        tokens = min(max_per_minute, tokens + refill)
        if tokens < 1:
            return {"ok": False, "retry_after_seconds": int(60 / max_per_minute) + 1}
        self._buckets[key] = (tokens - 1, now)
        return {"ok": True, "remaining": int(tokens - 1)}


_rate_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    return _rate_limiter


# === OAuth scaffold (real impl needs Auth0/Clerk/Google — placeholder) ===

class OAuthHandler:
    """OAuth 2.0 scaffold. Real impl plugs in Google/Microsoft/Auth0.

    Flow:
    1. Client redirects to /oauth/authorize?provider=google&state=...
    2. Provider redirects back to /oauth/callback?code=...&state=...
    3. We exchange code for tokens
    4. We create/update user record, mint our session token
    """

    def __init__(self):
        self._states: dict[str, float] = {}
        self._sessions: dict[str, dict] = {}

    def build_authorize_url(self, provider: str, redirect_uri: str,
                            client_id: str, scope: str = "openid email profile") -> str:
        state = uuid.uuid4().hex
        self._states[state] = time.time()
        # Real impl: provider-specific authorize URL
        return f"https://{provider}.com/oauth/authorize?response_type=code&client_id={client_id}&redirect_uri={redirect_uri}&scope={scope}&state={state}"

    def exchange_code(self, code: str, state: str) -> dict:
        if state not in self._states:
            return {"ok": False, "error": "invalid_state"}
        if time.time() - self._states[state] > 600:
            return {"ok": False, "error": "state_expired"}
        del self._states[state]
        # Real impl: POST to provider's token endpoint
        # Placeholder: create a session token
        session_token = f"oauth-{uuid.uuid4().hex}"
        self._sessions[session_token] = {
            "code": code, "created_at": time.time(),
        }
        return {"ok": True, "session_token": session_token}


_oauth = OAuthHandler()


def get_oauth() -> OAuthHandler:
    return _oauth


# === Vault abstraction (real impl uses HashiCorp Vault / AWS Secrets Manager) ===

class SecretsVault:
    """Simple in-memory vault. Production: replace with Vault/AWS SM."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._load_from_disk()

    def _load_from_disk(self):
        d = Path("/workspace/orchestrator/keys")
        if not d.exists():
            return
        for f in d.glob(".nvidia_*.txt"):
            self._store[f.stem.lstrip(".")] = f.read_text().strip()
        for f in d.glob(".twilio_*.txt"):
            self._store[f.stem.lstrip(".")] = f.read_text().strip()
        for f in d.glob(".sendgrid_*.txt"):
            self._store[f.stem.lstrip(".")] = f.read_text().strip()
        for f in d.glob(".apollo_*.txt"):
            self._store[f.stem.lstrip(".")] = f.read_text().strip()
        for f in d.glob(".hunter_*.txt"):
            self._store[f.stem.lstrip(".")] = f.read_text().strip()
        # API keys
        for f in d.glob(".orchestrator_api_key.txt"):
            self._store["orchestrator_api_key"] = f.read_text().strip()
        for f in d.glob(".webhook_signing_key.txt"):
            self._store["webhook_signing_key"] = f.read_text().strip()

    def get(self, name: str) -> str | None:
        return self._store.get(name)

    def put(self, name: str, value: str) -> None:
        self._store[name] = value

    def list_keys(self) -> list[str]:
        return list(self._store.keys())


_vault = SecretsVault()


def get_vault() -> SecretsVault:
    return _vault


SOURCE = "Production security — HMAC webhook signing, idempotency table, rate limiter, OAuth scaffold, secrets vault"
