"""Company enrichment.

Demo mode uses a deterministic mock derived from the email domain (so the
pipeline is fully runnable offline). A real provider (Clearbit/Apollo/etc.)
slots in behind the same `Enricher` protocol when a key is configured.
"""

from __future__ import annotations

from typing import Protocol

from ..models import EnrichmentResult, Lead
from ..normalize import is_business_domain

# Deterministic pseudo-enrichment so demos look real without a paid API.
_INDUSTRY_HINTS: dict[str, str] = {
    "io": "Software / SaaS",
    "ai": "Artificial Intelligence",
    "dev": "Software / Developer Tools",
    "health": "Healthcare",
    "shop": "E-commerce / Retail",
    "store": "E-commerce / Retail",
    "law": "Legal Services",
    "capital": "Finance",
}
_SIZE_BUCKETS = ("1-10", "11-50", "51-200", "201-500", "501-1000")


class Enricher(Protocol):
    name: str

    async def enrich(self, lead: Lead) -> EnrichmentResult: ...


class MockEnricher:
    """Offline, deterministic enrichment — keyless, good enough to demo."""

    name = "mock"

    async def enrich(self, lead: Lead) -> EnrichmentResult:
        domain = lead.domain or ""
        if not domain or not is_business_domain(domain):
            return EnrichmentResult(domain=domain or None, source=self.name)

        tld = domain.rsplit(".", 1)[-1]
        industry = next((v for k, v in _INDUSTRY_HINTS.items() if k in domain), "General Business")
        # Stable bucket from a cheap hash of the domain — deterministic per lead.
        bucket = _SIZE_BUCKETS[sum(map(ord, domain)) % len(_SIZE_BUCKETS)]
        name = lead.company or domain.rsplit(".", 1)[0].replace("-", " ").title()
        return EnrichmentResult(
            domain=domain,
            company_name=name,
            industry=industry,
            employee_range=bucket,
            summary=f"{name} — {industry} (~{bucket} employees, .{tld}).",
            source=self.name,
        )


def get_enricher() -> Enricher:
    """Today: the mock. Real providers register here behind their keys."""
    return MockEnricher()
