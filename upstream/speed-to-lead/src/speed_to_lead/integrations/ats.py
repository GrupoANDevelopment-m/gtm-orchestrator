"""ATS / HRIS connector — the same qualify→route pipeline, for recruiting.

Lead qualification and candidate qualification are structurally identical, so the
same engine can run an applicant funnel: push a qualified inbound "candidate" to
Greenhouse (the accessible ATS). Workday / SAP SuccessFactors / Paychex field
mappings are documented in `docs/ats-mapping.md`.
"""

from __future__ import annotations

import base64
from typing import Protocol

import httpx

from ..config import Settings
from ..logging import get_logger
from ..models import Lead, QualificationResult

log = get_logger(__name__)


class Ats(Protocol):
    provider: str

    async def add_candidate(self, lead: Lead, qual: QualificationResult) -> str | None: ...


def _split_name(name: str | None) -> tuple[str, str]:
    if not name:
        return "Unknown", ""
    parts = name.split()
    return parts[0], " ".join(parts[1:])


class GreenhouseAts:
    """Greenhouse Harvest API — add a candidate from a qualified lead."""

    provider = "greenhouse"
    _BASE = "https://harvest.greenhouse.io/v1"

    def __init__(self, api_key: str, on_behalf_of: str) -> None:
        token = base64.b64encode(f"{api_key}:".encode()).decode()  # Harvest uses Basic auth
        self._headers = {"Authorization": f"Basic {token}", "On-Behalf-Of": on_behalf_of}

    def candidate_payload(self, lead: Lead, qual: QualificationResult) -> dict[str, object]:
        first, last = _split_name(lead.name)
        return {
            "first_name": first,
            "last_name": last or "—",
            "company": lead.company,
            "email_addresses": [{"value": lead.email, "type": "personal"}],
            "tags": [f"intent:{qual.intent}", f"tier:{qual.tier.value}"],
            "notes": "; ".join(qual.reasons),
        }

    async def add_candidate(self, lead: Lead, qual: QualificationResult) -> str | None:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self._BASE}/candidates",
                headers=self._headers,
                json=self.candidate_payload(lead, qual),
            )
            resp.raise_for_status()
        created_id = resp.json().get("id")
        return str(created_id) if created_id else None


def get_ats(settings: Settings) -> Ats | None:
    """Greenhouse when configured, else None (ATS routing is opt-in)."""
    if settings.greenhouse_api_key and settings.greenhouse_on_behalf_of:
        return GreenhouseAts(settings.greenhouse_api_key, settings.greenhouse_on_behalf_of)
    return None
