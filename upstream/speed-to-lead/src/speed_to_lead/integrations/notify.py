"""Notification adapters: Slack ping + email send. Console is the keyless default."""

from __future__ import annotations

from typing import Protocol

import httpx

from ..config import Settings
from ..logging import get_logger
from ..models import DraftResult, Lead, QualificationResult

log = get_logger(__name__)


class Notifier(Protocol):
    channel: str

    async def notify(self, lead: Lead, qual: QualificationResult, draft: DraftResult) -> bool: ...


class ConsoleNotifier:
    channel = "console"

    async def notify(self, lead: Lead, qual: QualificationResult, draft: DraftResult) -> bool:
        log.info(
            "notify.console",
            email=lead.email,
            tier=qual.tier.value,
            subject=draft.subject,
            requires_review=draft.requires_review,
        )
        return True


class SlackNotifier:
    """Post a new-lead alert to a Slack incoming webhook."""

    channel = "slack"

    def __init__(self, webhook_url: str) -> None:
        self._url = webhook_url

    async def notify(self, lead: Lead, qual: QualificationResult, draft: DraftResult) -> bool:
        emoji = {"hot": "🔥", "warm": "🌤️", "cold": "❄️", "spam": "🚫"}.get(qual.tier.value, "•")
        text = (
            f"{emoji} *New {qual.tier.value.upper()} lead* — {lead.name or lead.email} "
            f"({lead.company or lead.domain})\n"
            f"Intent: *{qual.intent}* · score {qual.score:.2f} · {', '.join(qual.reasons[:3])}\n"
            f"Draft {'queued for review' if draft.requires_review else 'auto-sent'}: "
            f"_{draft.subject}_"
        )
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(self._url, json={"text": text})
            resp.raise_for_status()
        return True


def get_notifier(settings: Settings) -> Notifier:
    if settings.slack_enabled and settings.slack_webhook_url:
        return SlackNotifier(settings.slack_webhook_url)
    return ConsoleNotifier()
