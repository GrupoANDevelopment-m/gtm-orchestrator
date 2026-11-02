"""
prospec adapter — REAL LeadIntelligenceService ported from prospex/apps/api/src/ai/lead-intelligence.service.ts

This is the actual scoring logic from prospex, ported to Python so we can use
it without running NestJS. The scoring is identical to the TypeScript original:
- Base 50, plus bonuses for phone/website/address/rating/reviews
- Bayesian rating: pulls rating toward PRIOR_MEAN (4.0) with PRIOR_WEIGHT (25)
- Industry-specific bonuses from INDUSTRY_SCORES
- City-specific bonuses from CITY_SCORES (Indonesian cities: Jakarta=95, etc)
- No-website bonus (digital gap opportunity)
- Final score clamped to [0, 100], tier = HIGH (>=75), MEDIUM (>=55), LOW

Returns: { score, priority, category, factors, recommendation }
"""
from __future__ import annotations

INDUSTRY_SCORES = {
    "restaurant": 85, "cafe": 90, "retail": 75, "automotive": 80, "healthcare": 85,
    "beauty": 80, "education": 75, "realestate": 85, "event": 80, "tech": 90,
    "professional": 85, "hotel": 80, "spa": 78, "gym": 75,
    # Aliases
    "saas": 90, "software": 90, "fintech": 88, "consulting": 85, "manufacturing": 75,
    "logistics": 78, "education": 75, "construction": 75, "agriculture": 70,
    "realestate": 85, "foodbeverage": 88, "fb": 88, "retailstore": 75,
}

CITY_SCORES = {
    "jakarta": 95, "surabaya": 85, "bandung": 80, "medan": 75, "yogyakarta": 78,
    "semarang": 72, "makassar": 70, "palembang": 68, "pekanbaru": 65, "denpasar": 85,
    # Common cities
    "newyork": 95, "sanfrancisco": 95, "london": 92, "berlin": 85, "paris": 88,
    "tokyo": 90, "singapore": 92, "saopaulo": 85, "mexicocity": 80, "lisbon": 78,
    "miami": 82, "boston": 88, "seattle": 90, "austin": 85, "toronto": 85,
    "sydney": 85, "dubai": 88,
}


def _normalize(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalpha())


def _find_city_bonus(address: str) -> tuple[int, str | None]:
    if not address:
        return 0, None
    addr_lower = address.lower()
    # Remove spaces for matching "new york" vs "newyork"
    addr_no_space = addr_lower.replace(" ", "")
    for city, city_score in CITY_SCORES.items():
        if city in addr_lower or city in addr_no_space:
            return (city_score - 50) // 10, city
    return 0, None


def score_lead(lead: dict, target_industry: str | None = None) -> dict:
    """REAL prospex LeadIntelligenceService.scoreLead() — exact port from TypeScript.
    Lead dict keys: name, address, phone, website, rating, reviewCount, hasWebsite, industry
    Returns: { score, priority, category, factors, recommendation }
    """
    score = 50
    factors: list[str] = []

    phone = lead.get("phone") or ""
    website = lead.get("website") or ""
    has_website = lead.get("hasWebsite") or lead.get("has_website")
    address = lead.get("address") or ""

    if phone:
        score += 10
        factors.append("Has phone number")
    if website or has_website:
        score += 10
        factors.append("Has website")
    if address:
        score += 5
        factors.append("Has address")

    raw_rating_str = str(lead.get("rating") or "0")
    try:
        raw_rating = float(raw_rating_str) if raw_rating_str else 0.0
    except ValueError:
        raw_rating = 0.0

    if raw_rating > 0:
        # Bayesian average (REAL prospex logic)
        PRIOR_MEAN = 4.0
        PRIOR_WEIGHT = 25
        review_count = lead.get("reviewCount") or lead.get("review_count") or 1
        try:
            review_count = int(review_count)
        except (ValueError, TypeError):
            review_count = 1
        rating = (PRIOR_WEIGHT * PRIOR_MEAN + review_count * raw_rating) / (PRIOR_WEIGHT + review_count)

        if rating >= 4.5:
            score += 15
        elif rating >= 4.0:
            score += 10
        elif rating >= 3.5:
            score += 5

        count_label = f" ({review_count} reviews)" if review_count else ""
        factors.append(f"Rating: {raw_rating}★{count_label}")

        if review_count >= 100:
            score += 5
            factors.append("Well-established (100+ reviews)")
        elif review_count >= 20:
            score += 2
            factors.append("Established (20+ reviews)")

    industry_key = _normalize(target_industry or lead.get("industry") or "")
    if industry_key in INDUSTRY_SCORES:
        industry_score = INDUSTRY_SCORES[industry_key]
        bonus = (industry_score - 50) / 5
        score += bonus
        factors.append(f"High-value industry: {target_industry or lead.get('industry')}")

    city_bonus, city = _find_city_bonus(address)
    if city_bonus > 0:
        score += city_bonus
        factors.append(f"Prime location: {city}")

    if not has_website and not website:
        score += 5
        factors.append("No website (digital gap opportunity)")

    score = max(0, min(100, round(score)))
    priority = "HIGH" if score >= 75 else "MEDIUM" if score >= 55 else "LOW"
    category = target_industry or lead.get("industry") or "General Business"

    if priority == "HIGH":
        recommendation = f"High-value {category} business. Prioritize outreach immediately."
    elif priority == "MEDIUM":
        recommendation = f"Moderate potential. Include in standard outreach sequence."
    else:
        recommendation = f"Lower priority. Contact only if capacity allows."

    return {
        "score": score,
        "priority": priority,
        "category": category,
        "factors": factors,
        "recommendation": recommendation,
    }


def score_leads(leads: list[dict], target_industry: str | None = None) -> list[dict]:
    """Score multiple leads, return sorted by score descending."""
    scored = []
    for lead in leads:
        result = score_lead(lead, target_industry)
        scored.append({**lead, **result})
    scored.sort(key=lambda x: -x["score"])
    return scored


# Source identification
SOURCE = "REAL prospex LeadIntelligenceService (apps/api/src/ai/lead-intelligence.service.ts, ported from TypeScript to Python)"
