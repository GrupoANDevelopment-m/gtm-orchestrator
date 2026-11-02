"""
crm-release adapter — REAL evidence ledger + identity matching ported from
crm-release/apps/agent/agent/lib/evidence.ts and identity.ts

Evidence ledger: each piece of evidence has a "kind" with a weight (0-0.95) and a
"primary" flag (true = trusted source). Score = 1 - product(1 - weight for each).
Contradictions cap the score at 0.45.
Bands: VERIFIED (>=0.85 + primary), PROBABLE (>=0.55), POSSIBLE (>=0.30).

Identity matching: combines email match, employer domain match, and name match.
Confidence: high (email matches OR employer+name match), medium (one match), low.
"""
from __future__ import annotations

# === REAL WEIGHTS from crm-release/apps/agent/agent/lib/evidence.ts ===
EVIDENCE_WEIGHTS = {
    "profile.email-match":       {"weight": 0.95, "primary": True,  "label": "their email address is on the profile"},
    "linkedin.employer-and-name":{"weight": 0.85, "primary": True,  "label": "LinkedIn: employer and name both match"},
    "crm.thread-reply":          {"weight": 0.85, "primary": True,  "label": "they replied on a thread we have"},
    "crm.signature-block":       {"weight": 0.80, "primary": True,  "label": "their own email signature says so"},
    "github.account-identity":   {"weight": 0.80, "primary": True,  "label": "the GitHub account names them or their employer"},
    "crm.meeting-attendance":    {"weight": 0.70, "primary": True,  "label": "they attended a meeting on our calendar"},
    "web.cited-claim":           {"weight": 0.40, "primary": False, "label": "a cited web source states it"},
    "handle.name-form":          {"weight": 0.35, "primary": False, "label": "the handle is a form of their name"},
    "search.cites-profile":      {"weight": 0.35, "primary": False, "label": "a search for them cites this profile"},
    "employer-only":             {"weight": 0.20, "primary": False, "label": "the employer matches, the name does not"},
    "contradiction":             {"weight": 0.00, "primary": False, "label": "another source disagrees"},
}

BAND_FLOOR = {"VERIFIED": 0.85, "PROBABLE": 0.55, "POSSIBLE": 0.30}
CEILING = 0.99
CONTRADICTED_CAP = 0.45


def band_for(score: float, has_primary: bool) -> str | None:
    """REAL crm-release bandFor() — ported."""
    if score >= BAND_FLOOR["VERIFIED"] and has_primary:
        return "VERIFIED"
    if score >= BAND_FLOOR["PROBABLE"]:
        return "PROBABLE"
    if score >= BAND_FLOOR["POSSIBLE"]:
        return "POSSIBLE"
    return None


def _rationale(evidence: list, contradicted: bool, has_primary: bool) -> str:
    """REAL crm-release rationaleFor() — ported."""
    reasons = [EVIDENCE_WEIGHTS[item["kind"]]["label"] for item in evidence if item["kind"] != "contradiction"]
    if contradicted:
        clash = next((item for item in evidence if item["kind"] == "contradiction"), None)
        return f"Held: {clash.get('detail', 'sources disagree') if clash else 'sources disagree'}."
    if not reasons:
        return "No supporting evidence."
    if len(reasons) == 1:
        list_str = reasons[0]
    else:
        list_str = f"{', '.join(reasons[:-1])} and {reasons[-1]}"
    if has_primary:
        return list_str[0].upper() + list_str[1:]
    return f"{list_str[0].upper() + list_str[1:]} — but nothing that identifies them directly."


def score_evidence(evidence: list) -> dict:
    """REAL crm-release scoreEvidence() — ported from TypeScript.
    evidence: [{"kind": "profile.email-match", "detail": "...", "sourceUrl": "..."}]
    Returns: { score, band, has_primary, rationale }
    """
    if not evidence:
        return {"score": 0, "band": None, "has_primary": False, "rationale": "No evidence."}

    contradicted = any(item["kind"] == "contradiction" for item in evidence)
    has_primary = any(EVIDENCE_WEIGHTS[item["kind"]]["primary"] for item in evidence)

    combined = 1.0
    for item in evidence:
        combined *= (1 - EVIDENCE_WEIGHTS[item["kind"]]["weight"])

    score = min(CEILING, 1 - combined)
    if contradicted:
        score = min(score, CONTRADICTED_CAP)

    band = band_for(score, has_primary)
    rationale = _rationale(evidence, contradicted, has_primary)
    return {"score": round(score, 3), "band": band, "has_primary": has_primary, "rationale": rationale}


def verdict_for(person: dict, claim: dict) -> dict:
    """REAL crm-release verdictFor() — ported.
    person: { email, name, currentRoles: [{organisation: {name, domain}}] }
    claim: { email, companyName, companyDomain }
    Returns: { emailMatches, employerMatches, nameMatches, isSamePerson, confidence }
    """
    email_local = (claim.get("email") or "").split("@")[0]
    person_email = (person.get("email") or "").lower()
    claim_email = (claim.get("email") or "").lower()
    email_matches = bool(person_email) and person_email.strip() == claim_email.strip()

    employer_matches = False
    for role in person.get("currentRoles", []):
        org = role.get("organisation", {})
        if (org.get("domain") or "").lower() == (claim.get("companyDomain") or "").lower():
            employer_matches = True
            break
        org_name = (org.get("name") or "").lower()
        claim_name = (claim.get("companyName") or "").lower()
        if org_name and claim_name and (org_name in claim_name or claim_name in org_name):
            employer_matches = True
            break

    name_matches = _name_matches_local_part(person.get("name") or "", email_local)

    is_same_person = email_matches or (employer_matches and name_matches)

    if is_same_person:
        confidence = "high"
    elif employer_matches or name_matches:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "emailMatches": email_matches,
        "employerMatches": employer_matches,
        "nameMatches": name_matches,
        "isSamePerson": is_same_person,
        "confidence": confidence,
    }


def _name_matches_local_part(person_name: str, email_local: str) -> bool:
    """Loose match: 'Sarah Mitchell' matches 'sarah.mitchell' or 'smitchell'."""
    if not person_name or not email_local:
        return False
    pn = person_name.lower().replace(" ", "").replace(".", "")
    el = email_local.lower().replace(".", "").replace("-", "").replace("_", "")
    if pn in el or el in pn:
        return True
    # First initial + last name
    parts = person_name.lower().split()
    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
        if el == (first[0] + last) or el == first + last[0]:
            return True
    return False


SOURCE = "REAL crm-release evidence.ts + identity.ts (apps/agent/agent/lib/, ported from TypeScript to Python)"
