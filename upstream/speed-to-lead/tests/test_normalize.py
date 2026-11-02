"""Normalization: domain parsing, business-vs-free email, field coalescing."""

from speed_to_lead.models import InboundLead
from speed_to_lead.normalize import email_domain, is_business_domain, normalize_lead


def test_email_domain() -> None:
    assert email_domain("a@Example.COM") == "example.com"


def test_business_vs_free_domain() -> None:
    assert is_business_domain("acme.com")
    assert not is_business_domain("gmail.com")


def test_normalize_infers_company_from_business_domain() -> None:
    lead = normalize_lead(InboundLead(email="jane@north-wind.io"))
    assert lead.domain == "north-wind.io"
    assert lead.company == "North Wind"
    assert lead.source == "direct"


def test_normalize_keeps_free_domain_company_none() -> None:
    lead = normalize_lead(InboundLead(email="someone@gmail.com"))
    assert lead.company is None


def test_normalize_reads_utm_source_from_raw() -> None:
    lead = normalize_lead(InboundLead.model_validate({"email": "x@acme.com", "utm_source": "ads"}))
    assert lead.source == "ads"
