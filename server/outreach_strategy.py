"""
Outreach strategy — pick the best channel for a lead given their profile + context.

Used by the cognitive loop Layer 3 (Goal Engine) and Layer 4 (Reasoning) to
plan outreach. Returns ranked channel recommendations.

Factors:
  - lead profile (phone, email, telegram, etc.)
  - channel live status (which APIs have keys)
  - historical engagement (open_rate, reply_rate from memory)
  - lead geography (CN → WeChat; BR → WhatsApp; etc.)
  - lead industry (B2B SaaS → LinkedIn > Twitter)
"""
from __future__ import annotations

import json
import os
from typing import Any


# Channel scoring weights
_GEO_AFFINITY = {
    # country_code (lower) -> ordered channel preference
    "br": ["whatsapp", "instagram", "email", "linkedin"],
    "in": ["whatsapp", "email", "linkedin"],
    "mx": ["whatsapp", "email", "linkedin"],
    "ar": ["whatsapp", "instagram", "linkedin"],
    "us": ["email", "linkedin", "twitter", "discord"],
    "ca": ["email", "linkedin", "twitter"],
    "gb": ["email", "linkedin", "twitter"],
    "de": ["email", "linkedin", "twitter"],
    "fr": ["email", "linkedin", "twitter"],
    "es": ["email", "linkedin", "instagram"],
    "cn": ["wechat", "email", "linkedin"],   # wechat not supported; skip
    "jp": ["email", "twitter", "linkedin"],
    "kr": ["email", "discord", "twitter"],
    "au": ["email", "linkedin", "twitter"],
    "ng": ["whatsapp", "email"],
    "za": ["whatsapp", "email", "linkedin"],
    "ae": ["whatsapp", "linkedin", "email"],
    "global": ["email", "linkedin", "twitter", "discord"],
}

# Industry affinity
_INDUSTRY_AFFINITY = {
    "saas":        ["linkedin", "email", "twitter", "discord"],
    "b2b":         ["linkedin", "email", "twitter"],
    "ecommerce":   ["instagram", "whatsapp", "email"],
    "fintech":     ["linkedin", "email"],
    "healthcare":  ["email", "linkedin"],
    "realestate":  ["whatsapp", "email", "instagram"],
    "education":   ["email", "discord", "twitter"],
    "gaming":      ["discord", "twitter", "email"],
    "crypto":      ["discord", "twitter", "telegram"],
    "creator":     ["instagram", "twitter", "email"],
}


def pick_outreach_channels(lead: dict, *, max_channels: int = 3,
                           available_channels: list[str] | None = None
                           ) -> list[dict]:
    """Return ranked list of {channel, score, reason, available} for a lead.

    Args:
      lead: {country, industry, phone, email, telegram_id, discord_id,
             linkedin_url, instagram_handle, ...}
      max_channels: max recommendations to return
      available_channels: list of channels currently 'live' (default: all)
    """
    # Lead's reachable channels (which IDs do they actually have?)
    reachable = []
    if lead.get("phone"):
        reachable.append(("whatsapp", "phone"))
    if lead.get("telegram_id"):
        reachable.append(("telegram", "telegram_id"))
    if lead.get("discord_id"):
        reachable.append(("discord", "discord_id"))
    if lead.get("linkedin_url") or lead.get("linkedin_urn"):
        reachable.append(("linkedin", "linkedin"))
    if lead.get("instagram_handle") or lead.get("instagram_igsid"):
        reachable.append(("instagram", "instagram"))
    if lead.get("email"):
        reachable.append(("email", "email"))  # always available via SendGrid

    if not reachable:
        return []

    # Scoring: geo > industry > channel-specific multiplier
    geo = (lead.get("country") or "global").lower()
    industry = (lead.get("industry") or "b2b").lower()
    geo_pref = _GEO_AFFINITY.get(geo, _GEO_AFFINITY["global"])
    ind_pref = _INDUSTRY_AFFINITY.get(industry, _INDUSTRY_AFFINITY["b2b"])

    ranked = []
    for channel, via in reachable:
        score = 50  # base
        if channel in geo_pref:
            score += (len(geo_pref) - geo_pref.index(channel)) * 10
        if channel in ind_pref:
            score += (len(ind_pref) - ind_pref.index(channel)) * 8
        # channel-specific bonuses
        if channel == "email":
            score += 30  # always works, low friction
        if channel == "linkedin" and industry in ("saas", "b2b", "fintech"):
            score += 20
        if channel == "whatsapp" and geo in ("br", "in", "mx", "ar", "ng", "za", "ae"):
            score += 25
        if channel == "instagram" and industry in ("ecommerce", "creator", "realestate"):
            score += 15

        # Live-status adjustment
        is_available = (channel in (available_channels or [])
                        or channel == "email")
        if not is_available:
            score -= 40

        ranked.append({
            "channel": channel,
            "score": score,
            "reason": f"reachable via {via}, "
                      f"geo={geo} (rank {geo_pref.index(channel)+1 if channel in geo_pref else 'n/a'}), "
                      f"industry={industry} (rank {ind_pref.index(channel)+1 if channel in ind_pref else 'n/a'})",
            "available": is_available,
            "via": via,
        })

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked[:max_channels]


def plan_outreach(lead: dict, body: str, *, subject: str | None = None,
                  max_channels: int = 2,
                  available_channels: list[str] | None = None) -> dict:
    """Return an outreach plan: ranked channels + adapted bodies per channel.

    Adapts body per channel:
      - WhatsApp/SMS: keep under 160 chars, friendly, 1 CTA
      - LinkedIn: under 300 chars, professional, 1 question
      - Twitter: under 280 chars, hashtags, 1 link
      - Email: keep full body, add subject if missing
      - Discord: under 2000 chars, can use markdown
      - Telegram: can use markdown, under 4096 chars
    """
    ranked = pick_outreach_channels(lead, max_channels=max_channels,
                                    available_channels=available_channels)
    if not ranked:
        return {"ok": False, "error": "no_reachable_channels",
                "hint": "Provide at least one of: phone, telegram_id, discord_id, linkedin_url, instagram_handle, email"}

    adaptations = {
        "whatsapp": lambda b: b[:160] if len(b) > 160 else b,
        "email": lambda b: b,  # email keeps full body
        "linkedin": lambda b: (b[:280] + "...") if len(b) > 280 else b,
        "twitter": lambda b: (b[:260] + "...") if len(b) > 260 else b,
        "discord": lambda b: b[:2000],
        "telegram": lambda b: b[:4096],
        "instagram": lambda b: (b[:1000] + "...") if len(b) > 1000 else b,
    }

    plan = {"ok": True, "lead_id": lead.get("id"),
            "ranked": ranked,
            "primary": None,
            "secondary": None,
            "bodies": {}}

    # Best available channel is primary
    available_ranked = [r for r in ranked if r["available"]]
    if available_ranked:
        plan["primary"] = available_ranked[0]
        plan["bodies"][available_ranked[0]["channel"]] = (
            adaptations.get(available_ranked[0]["channel"], lambda b: b)(body)
        )
        if len(available_ranked) > 1:
            plan["secondary"] = available_ranked[1]
            plan["bodies"][available_ranked[1]["channel"]] = (
                adaptations.get(available_ranked[1]["channel"], lambda b: b)(body)
            )
    else:
        plan["primary"] = ranked[0]
        plan["error"] = "no_available_channels_use_email_fallback"
        plan["hint"] = (
            f"Top channel '{ranked[0]['channel']}' not live. "
            f"Set credentials in keys/ or use email fallback."
        )

    return plan
