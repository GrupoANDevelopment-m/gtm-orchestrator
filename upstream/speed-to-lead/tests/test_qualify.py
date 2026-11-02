"""Rule qualifier: intent detection, spam filtering, tiering."""

from speed_to_lead.models import EnrichmentResult, FitTier, Lead
from speed_to_lead.services.qualify import RuleQualifier, detect_intent, looks_like_spam


def _lead(message: str, *, email: str = "buyer@acme.com", company: str | None = "Acme") -> Lead:
    return Lead(id="t", email=email, company=company, domain=email.split("@")[1], message=message)


def test_detect_intent() -> None:
    assert detect_intent("what is your pricing?") == "pricing"
    assert detect_intent("ready to buy now") == "purchase_intent"
    assert detect_intent("can we book a demo") == "demo_request"
    assert detect_intent("hello") == "general_inquiry"


def test_spam_detection() -> None:
    assert looks_like_spam("we sell SEO services and backlinks")
    assert not looks_like_spam("interested in your pricing")


def test_hot_lead() -> None:
    res = RuleQualifier().qualify(_lead("need pricing for 40 seats"), EnrichmentResult())
    assert res.tier is FitTier.HOT
    assert res.score >= 0.7
    assert res.reasons


def test_spam_lead_short_circuits() -> None:
    res = RuleQualifier().qualify(_lead("buy cheap backlinks SEO services"), EnrichmentResult())
    assert res.tier is FitTier.SPAM
    assert res.confidence >= 0.8


def test_job_inquiry_is_filtered() -> None:
    res = RuleQualifier().qualify(_lead("do you have an internship position?"), EnrichmentResult())
    assert res.tier is FitTier.SPAM


def test_free_email_vague_is_cold() -> None:
    res = RuleQualifier().qualify(
        _lead("just curious", email="x@gmail.com", company=None), EnrichmentResult()
    )
    assert res.tier is FitTier.COLD
