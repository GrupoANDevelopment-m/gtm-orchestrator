"""CRM adapters. `ConsoleCrm` is keyless; Twenty/HubSpot are real HTTP."""

from __future__ import annotations

from typing import Protocol

import httpx

from ..config import Settings
from ..logging import get_logger
from ..models import Lead, QualificationResult

log = get_logger(__name__)


class Crm(Protocol):
    provider: str

    async def upsert_person(self, lead: Lead, qual: QualificationResult) -> str | None: ...


class ConsoleCrm:
    """Keyless default — logs the upsert instead of calling a real CRM."""

    provider = "console"

    async def upsert_person(self, lead: Lead, qual: QualificationResult) -> str | None:
        log.info(
            "crm.upsert",
            provider=self.provider,
            email=lead.email,
            tier=qual.tier.value,
            score=qual.score,
            intent=qual.intent,
        )
        return f"console-{lead.id}"


class TwentyCrm:
    """Twenty CRM (self-hosted) via its REST API."""

    provider = "twenty"

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def upsert_person(self, lead: Lead, qual: QualificationResult) -> str | None:
        payload = {
            "emails": {"primaryEmail": lead.email},
            "name": {"firstName": (lead.name or "").split(" ")[0], "lastName": ""},
            "companyName": lead.company,
            "leadScore": qual.score,
            "leadTier": qual.tier.value,
            "leadIntent": qual.intent,
            "source": lead.source,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self._base_url}/rest/people", headers=self._headers, json=payload
            )
            resp.raise_for_status()
            data = resp.json()
        person_id = data.get("data", {}).get("id") or data.get("id")
        return str(person_id) if person_id else None


class HubSpotCrm:
    """HubSpot contacts via the v3 CRM API."""

    provider = "hubspot"

    def __init__(self, api_key: str) -> None:
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def upsert_person(self, lead: Lead, qual: QualificationResult) -> str | None:
        props = {
            "email": lead.email,
            "firstname": (lead.name or "").split(" ")[0],
            "company": lead.company or "",
            "hs_lead_status": qual.tier.value.upper(),
            "lead_score": str(qual.score),
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.hubapi.com/crm/v3/objects/contacts",
                headers=self._headers,
                json={"properties": props},
            )
            resp.raise_for_status()
        created_id = resp.json().get("id")
        return str(created_id) if created_id else None


def get_crm(settings: Settings) -> Crm:
    """Pick the configured CRM; fall back to console when keys are absent."""
    if not settings.demo_mode:
        if (
            settings.crm_provider == "twenty"
            and settings.twenty_api_url
            and settings.twenty_api_key
        ):
            return TwentyCrm(settings.twenty_api_url, settings.twenty_api_key)
        if settings.crm_provider == "hubspot" and settings.hubspot_api_key:
            return HubSpotCrm(settings.hubspot_api_key)
    return ConsoleCrm()
