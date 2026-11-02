"""Greenhouse ATS connector: field mapping + auth header (no network)."""

import base64

from speed_to_lead.config import Settings
from speed_to_lead.integrations.ats import GreenhouseAts, get_ats
from speed_to_lead.models import FitTier, Lead, QualificationResult


def _qual() -> QualificationResult:
    return QualificationResult(
        tier=FitTier.HOT, score=0.9, confidence=0.9, intent="pricing", reasons=["business domain"]
    )


def test_candidate_payload_maps_fields() -> None:
    ats = GreenhouseAts("key123", "42")
    lead = Lead(id="t", email="jane@acme.com", name="Jane Doe", company="Acme", domain="acme.com")
    payload = ats.candidate_payload(lead, _qual())
    assert payload["first_name"] == "Jane"
    assert payload["last_name"] == "Doe"
    assert payload["email_addresses"] == [{"value": "jane@acme.com", "type": "personal"}]
    assert payload["tags"] == ["intent:pricing", "tier:hot"]


def test_basic_auth_header() -> None:
    ats = GreenhouseAts("key123", "42")
    expected = base64.b64encode(b"key123:").decode()
    assert ats._headers["Authorization"] == f"Basic {expected}"
    assert ats._headers["On-Behalf-Of"] == "42"


def test_get_ats_is_opt_in() -> None:
    assert get_ats(Settings(greenhouse_api_key=None)) is None
    configured = Settings(greenhouse_api_key="k", greenhouse_on_behalf_of="42")
    assert get_ats(configured) is not None
