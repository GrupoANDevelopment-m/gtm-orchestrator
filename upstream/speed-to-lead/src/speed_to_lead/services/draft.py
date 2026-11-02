"""Draft a personalized first-touch reply.

`TemplateDrafter` is keyless and deterministic (demo default). `LlmDrafter`
uses any provider via litellm when a key is set. Both implement `Drafter`, so
the graph is provider-agnostic.
"""

from __future__ import annotations

from typing import Protocol

from ..config import Settings
from ..models import DraftResult, EnrichmentResult, Lead, QualificationResult

# Per-intent opening line — keeps the templated draft from feeling generic.
_INTENT_OPENERS: dict[str, str] = {
    "purchase_intent": "Thanks for reaching out — let's get you set up fast.",
    "pricing": "Happy to walk you through pricing and the right plan for {company}.",
    "demo_request": "I'd love to show you a quick demo tailored to {company}.",
    "support": "Thanks for flagging this — let's get it sorted quickly.",
    "partnership": "Appreciate you reaching out about working together.",
    "general_inquiry": "Thanks for getting in touch — happy to help.",
}


def _first_name(lead: Lead) -> str:
    if lead.name:
        return lead.name.split()[0]
    return "there"


class Drafter(Protocol):
    name: str

    async def draft(
        self, lead: Lead, enrichment: EnrichmentResult, qual: QualificationResult
    ) -> DraftResult: ...


class TemplateDrafter:
    """Keyless, deterministic reply. A solid fallback and the demo default."""

    name = "template"

    async def draft(
        self, lead: Lead, enrichment: EnrichmentResult, qual: QualificationResult
    ) -> DraftResult:
        company = lead.company or "your team"
        opener = _INTENT_OPENERS.get(qual.intent, _INTENT_OPENERS["general_inquiry"]).format(
            company=company
        )
        subject = f"Re: your message to us, {_first_name(lead)}"
        body = (
            f"Hi {_first_name(lead)},\n\n"
            f"{opener}\n\n"
            "Do you have 15 minutes this week? Here's my calendar: "
            "https://cal.com/your-handle\n\n"
            "Best,\nThe Team"
        )
        return DraftResult(subject=subject, body=body, channel="email", model=self.name)


class LlmDrafter:
    """Provider-agnostic LLM reply via litellm (BYO key)."""

    name = "llm"

    def __init__(self, model: str, api_key: str) -> None:
        self._model = model
        self._api_key = api_key

    async def draft(
        self, lead: Lead, enrichment: EnrichmentResult, qual: QualificationResult
    ) -> DraftResult:
        import litellm  # lazy: only imported when an LLM is actually configured

        system = (
            "You are an SDR writing the first reply to an inbound lead. Be warm, concise "
            "(<120 words), specific to their message, and end with one clear call to action "
            "(book a 15-min call). Plain text. Never invent facts about their company."
        )
        context = (
            f"Lead: {lead.name or 'unknown'} <{lead.email}> at {lead.company or 'unknown'}.\n"
            f"Their message: {lead.message or '(none)'}\n"
            f"Detected intent: {qual.intent}. Company: {enrichment.summary or 'n/a'}."
        )
        resp = await litellm.acompletion(
            model=self._model,
            api_key=self._api_key,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": context},
            ],
            max_tokens=350,
            temperature=0.6,
        )
        body = resp["choices"][0]["message"]["content"].strip()
        subject = f"Re: your message, {_first_name(lead)}"
        return DraftResult(subject=subject, body=body, channel="email", model=self._model)


def get_drafter(settings: Settings) -> Drafter:
    """LLM drafter when a key is configured and not in demo mode, else template."""
    if settings.llm_enabled and settings.llm_api_key:
        return LlmDrafter(model=settings.llm_model, api_key=settings.llm_api_key)
    return TemplateDrafter()
