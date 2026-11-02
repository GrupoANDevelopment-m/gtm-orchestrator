"""End-to-end LangGraph pipeline behavior (keyless / demo mode)."""

from speed_to_lead.agents import build_pipeline
from speed_to_lead.models import FitTier, Lead
from speed_to_lead.services.qualify import RuleQualifier


def _lead(message: str, email: str = "maria@northwind-logistics.com") -> Lead:
    return Lead(
        id="t", email=email, company="Northwind", domain=email.split("@")[1], message=message
    )


async def test_hot_lead_gets_drafted_and_routed() -> None:
    pipeline = build_pipeline(qualifier=RuleQualifier())
    out = await pipeline.run(_lead("need pricing and a demo for 40 people"))
    assert out.qualification.tier is FitTier.HOT
    assert out.draft.body  # a real draft was produced
    assert out.routing.crm_id is not None
    assert out.latency_ms is not None


async def test_spam_lead_is_discarded_not_drafted() -> None:
    pipeline = build_pipeline(qualifier=RuleQualifier())
    out = await pipeline.run(_lead("buy backlinks, SEO services, ranking boost"))
    assert out.qualification.tier is FitTier.SPAM
    assert out.draft.channel == "none"  # spam placeholder, no reply drafted
    assert not out.routing.reply_sent


async def test_low_confidence_requires_review() -> None:
    pipeline = build_pipeline(qualifier=RuleQualifier())
    out = await pipeline.run(_lead("just looking around", email="x@gmail.com"))
    # Cold/free-mail → not auto-sent, queued for a human.
    assert out.draft.requires_review
    assert not out.routing.reply_sent
