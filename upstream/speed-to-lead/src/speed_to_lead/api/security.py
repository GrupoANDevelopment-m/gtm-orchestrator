"""Webhook signature verification (HMAC-SHA256), constant-time."""

from __future__ import annotations

import hashlib
import hmac


def sign(secret: str, body: bytes) -> str:
    """Compute the hex signature a sender should send in `X-Signature`."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str | None, body: bytes, provided: str | None) -> bool:
    """True if signing is disabled (no secret) or the signature matches.

    Returns False when a secret is configured but the header is missing/wrong.
    """
    if not secret:
        return True  # signing not enforced
    if not provided:
        return False
    return hmac.compare_digest(sign(secret, body), provided.removeprefix("sha256="))
