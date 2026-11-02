"""Normalize a messy `InboundLead` into a canonical `Lead`.

This is the first ETL step: derive the email domain, coalesce names from
common field aliases, and stamp the source. Pure and easily unit-tested.
"""

from __future__ import annotations

import re
import uuid

from .models import InboundLead, Lead

_FREE_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "icloud.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "live.com",
        "msn.com",
    }
)

_NAME_ALIASES = ("name", "full_name", "fullname", "first_name", "contact_name")
_COMPANY_ALIASES = ("company", "company_name", "organization", "org", "business")


def email_domain(email: str) -> str:
    return email.split("@", 1)[1].lower().strip()


def is_business_domain(domain: str) -> bool:
    """A free-mail domain is not a business domain."""
    return domain not in _FREE_EMAIL_DOMAINS


def _coalesce(
    primary: str | None, extras: dict[str, object], aliases: tuple[str, ...]
) -> str | None:
    if primary:
        return primary.strip()
    for key in aliases:
        val = extras.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def normalize_lead(inbound: InboundLead) -> Lead:
    """Convert a raw inbound payload into the canonical `Lead`."""
    domain = email_domain(inbound.email)
    # Unknown webhook fields land in `model_extra`; merge them with explicit `raw`.
    extras: dict[str, object] = {**(inbound.model_extra or {}), **inbound.raw}
    company = _coalesce(inbound.company, extras, _COMPANY_ALIASES)
    if not company and is_business_domain(domain):
        # Infer a company name from the domain when none was provided.
        company = re.sub(r"\.[a-z.]+$", "", domain).replace("-", " ").title()

    return Lead(
        id=uuid.uuid4().hex[:12],
        email=inbound.email,
        name=_coalesce(inbound.name, extras, _NAME_ALIASES),
        company=company,
        domain=domain,
        message=inbound.message,
        source=str(inbound.source or extras.get("utm_source") or extras.get("source") or "direct"),
    )
