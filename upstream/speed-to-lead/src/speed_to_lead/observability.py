"""Optional LLM tracing via Langfuse (bring-your-own-key; no-op without keys).

When Langfuse keys are configured, every LLM draft call is traced (prompt,
completion, token cost, latency) through litellm's native Langfuse callback.
Without keys this is a silent no-op, so the default keyless path is unaffected.
"""

from __future__ import annotations

import os

from .config import Settings
from .logging import get_logger

log = get_logger(__name__)


def setup_tracing(settings: Settings) -> bool:
    """Enable Langfuse tracing if keys are set. Returns True when enabled."""
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return False
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
    if settings.langfuse_host:
        os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)
    try:
        import litellm

        callbacks = set(litellm.success_callback or [])
        callbacks.add("langfuse")
        litellm.success_callback = list(callbacks)
        log.info("tracing.enabled", provider="langfuse")
        return True
    except Exception as exc:  # litellm/langfuse not installed — degrade quietly
        log.warning("tracing.setup_failed", error=str(exc))
        return False
