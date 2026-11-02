"""Domain models — the canonical shapes that flow through the pipeline.

`InboundLead` is the messy thing a webhook hands us; everything downstream
speaks `Lead` and the typed result objects.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FitTier(StrEnum):
    """How well a lead matches the ideal-customer profile."""

    HOT = "hot"
    WARM = "warm"
    COLD = "cold"
    SPAM = "spam"


class InboundLead(BaseModel):
    """Raw lead as received from a form / Cal / Typeform / Zapier webhook.

    Deliberately permissive: unknown fields are preserved under `raw` so no
    source-specific payload is lost before normalization.
    """

    model_config = ConfigDict(extra="allow")

    # Length caps bound ReDoS / memory / prompt-injection blast radius on untrusted webhook input.
    email: EmailStr
    name: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    message: str | None = Field(default=None, max_length=5000)
    source: str | None = Field(default=None, max_length=200, description="utm_source / referrer")
    phone: str | None = Field(default=None, max_length=50)
    raw: dict[str, Any] = Field(default_factory=dict)


class Lead(BaseModel):
    """Canonical, normalized lead used everywhere downstream."""

    id: str
    email: EmailStr
    name: str | None = None
    company: str | None = None
    domain: str | None = None
    message: str | None = None
    source: str = "direct"
    received_at: datetime = Field(default_factory=_utcnow)


class EnrichmentResult(BaseModel):
    """What we learned about the lead's company (mock in demo mode)."""

    domain: str | None = None
    company_name: str | None = None
    industry: str | None = None
    employee_range: str | None = None
    summary: str | None = None
    icp_similarity: float | None = None  # cosine sim to ICP seeds (FAISS), if computed
    source: str = "mock"


class QualificationResult(BaseModel):
    """Output of the qualify node — tier, score, and *why*."""

    tier: FitTier
    score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    intent: str
    reasons: list[str] = Field(default_factory=list)
    model: str = "rules"  # "rules" | "lora-classifier" | "llm"


class DraftResult(BaseModel):
    """The personalized reply, plus whether it's safe to auto-send."""

    subject: str
    body: str
    channel: str = "email"
    requires_review: bool = True
    model: str = "stub"


class RouteResult(BaseModel):
    """Side-effects performed for the lead (CRM, notify, send)."""

    crm_id: str | None = None
    crm_provider: str = "console"
    notified: bool = False
    reply_sent: bool = False


class LeadOutcome(BaseModel):
    """The full, audit-friendly result of one lead through the pipeline."""

    lead: Lead
    enrichment: EnrichmentResult
    qualification: QualificationResult
    draft: DraftResult
    routing: RouteResult
    latency_ms: float | None = None
    completed_at: datetime = Field(default_factory=_utcnow)
