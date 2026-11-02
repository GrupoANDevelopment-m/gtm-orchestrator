"""Training data for the intent classifier.

We generate a synthetic, template-based corpus of inbound-lead messages (no API
keys, fully reproducible) for *training*, and hold out a small, hand-written
*realistic* set for *evaluation* — so the reported scores measure generalization
to unseen phrasing, not memorization of templates. The synthetic nature and its
limitations are documented in `MODEL_CARD.md`.
"""

from __future__ import annotations

import random

# The label space the classifier predicts (maps to FitTier downstream).
INTENT_LABELS: list[str] = [
    "purchase_intent",
    "pricing",
    "demo_request",
    "support",
    "partnership",
    "general_inquiry",
    "spam",
    "job_inquiry",
]
LABEL2ID: dict[str, int] = {label: i for i, label in enumerate(INTENT_LABELS)}
ID2LABEL: dict[int, str] = {i: label for label, i in LABEL2ID.items()}

_SLOTS: dict[str, list[str]] = {
    "product": [
        "your platform",
        "the API",
        "your tool",
        "this product",
        "your software",
        "the app",
    ],
    "team": [
        "our team",
        "a 40-person team",
        "our sales org",
        "my startup",
        "our 12 reps",
        "our 5-person team",
        "a growing startup",
        "our agency",
        "our 200-person company",
    ],
    "metric": ["pricing", "the cost", "a quote", "your plans", "the price", "a rate card"],
    "timeframe": ["this week", "ASAP", "today", "soon", "next week", "this month", "by Friday"],
    "issue": [
        "a bug",
        "an error",
        "a problem",
        "an outage",
        "a login issue",
        "a 500 error",
        "a sync failure",
        "a timeout",
    ],
}

_TEMPLATES: dict[str, list[str]] = {
    "purchase_intent": [
        "We're ready to get started with {product}. How do we sign up?",
        "Ready to buy — what's the next step to onboard {team}?",
        "Let's move forward. How do we purchase {product} for {team}?",
        "We've decided to go with you. Can we get set up {timeframe}?",
        "Where do I enter payment to activate {product}?",
        "We want to roll this out to {team} — how do we start?",
        "Signed off internally, ready to onboard {team}.",
        "How soon can we go live? We'd like to start {timeframe}.",
        "Send over the contract, we're ready to commit.",
        "We'd like to upgrade to a paid plan {timeframe}.",
        "Greenlit on our side — let's get {product} running.",
        "Ready to purchase. What do you need from {team}?",
    ],
    "pricing": [
        "Can you send {metric} for {team}?",
        "What does {product} cost for {team}?",
        "Looking for {metric} — what are your plans?",
        "How much is {product} per month?",
        "We're working out budget. What's {metric}?",
        "Do you have enterprise {metric} for {team}?",
        "What's the per-seat price for {team}?",
        "Is there annual {metric}?",
        "Ballpark {metric} for {team}?",
        "Comparing vendors — what do you charge {team}?",
        "Any volume discounts for {team}?",
        "What tier would {team} need, and what's the cost?",
    ],
    "demo_request": [
        "Could we book a demo of {product} {timeframe}?",
        "I'd love a walkthrough of {product} for {team}.",
        "Can we schedule a call to see {product} in action?",
        "Interested in a demo — do you have time {timeframe}?",
        "Can someone show us how {product} works?",
        "We'd like to trial {product} before deciding.",
        "Open to a quick demo {timeframe}?",
        "Can we get a guided tour of {product}?",
        "Would like to see {product} live with {team}.",
        "Set up a demo call for {team}?",
        "Can I get a sandbox to try {product}?",
        "Show me what {product} can do for {team}.",
    ],
    "support": [
        "We're hitting {issue} with {product}. Can you help?",
        "Something's broken — {issue} when we log in.",
        "Need help: {issue} is blocking {team}.",
        "Our integration stopped working, seeing {issue}.",
        "Can support look at {issue} on our account?",
        "We have {issue} and need it fixed {timeframe}.",
        "Getting {issue} every time we sync.",
        "Existing customer here — {issue} since this morning.",
        "How do I fix {issue}?",
        "{issue} is affecting {team}, please advise.",
        "Reporting a problem: {issue}.",
        "Your API keeps returning {issue}.",
    ],
    "partnership": [
        "Interested in a partnership between our companies.",
        "Could we explore an integration partnership with {product}?",
        "We'd like to discuss a reseller arrangement.",
        "Exploring a co-marketing collaboration — open to it?",
        "Our platform could integrate with {product}. Let's talk.",
        "Keen to discuss a strategic partnership.",
        "We run a community of {team} — partnership idea?",
        "Would you be open to an affiliate deal?",
        "Let's explore building an integration together.",
        "Proposing a joint go-to-market with {product}.",
        "We have an audience that fits {product} — collaborate?",
        "Interested in becoming a {product} partner.",
    ],
    "general_inquiry": [
        "Just curious what {product} does.",
        "Heard about you — what's this all about?",
        "Wanted to learn more about {product}.",
        "What problem does {product} solve?",
        "Came across your site, exploring options.",
        "No rush, just looking into {product}.",
        "Can you tell me more about {product}?",
        "What makes {product} different?",
        "Researching tools in this space.",
        "Saw you mentioned somewhere — what do you do?",
        "Trying to understand if {product} fits {team}.",
        "General question about how {product} works.",
    ],
    "spam": [
        "We offer SEO services and backlinks to boost your ranking.",
        "Increase your traffic 10x — guest post opportunity available.",
        "Buy cheap crypto now, limited offer, click https://x.io https://y.io",
        "Get more sales with our marketing service, visit our site.",
        "We can get you on page 1 of Google, guaranteed backlinks.",
        "Exclusive forex and casino offers just for you today.",
        "Boost your domain authority with our backlink packages.",
        "I can write guest posts for your blog at cheap rates.",
        "Limited time crypto investment, 200% returns guaranteed.",
        "We sell verified leads and email lists — interested?",
        "Rank #1 on Google with our SEO services, free audit.",
        "Make money fast with our affiliate forex program.",
    ],
    "job_inquiry": [
        "Do you have any open internship positions? Attaching my resume.",
        "I'm looking for a junior engineer role — are you hiring?",
        "Please find my CV attached, interested in a position.",
        "Any career openings on your team right now?",
        "I'd love to apply for a job at your company.",
        "Seeking an internship — here's my resume.",
        "Are you hiring engineers? I'd like to apply.",
        "Recent grad looking for a role, resume attached.",
        "Do you have a careers page or open positions?",
        "Interested in joining your team — any openings?",
        "Looking for work in this field — can I send my CV?",
        "Is there a position available for my background?",
    ],
}


def _fill(template: str, rng: random.Random) -> str:
    out = template
    for slot, options in _SLOTS.items():
        token = "{" + slot + "}"
        while token in out:
            out = out.replace(token, rng.choice(options), 1)
    return out


def generate(n_per_class: int = 260, seed: int = 13) -> list[tuple[str, str]]:
    """Generate a balanced synthetic training set of (message, intent_label).

    Collects unique slot-filled messages per class, then pads (with repeats) to
    keep classes balanced even where template diversity is naturally lower.
    """
    rng = random.Random(seed)
    rows: list[tuple[str, str]] = []
    for label, templates in _TEMPLATES.items():
        uniq: list[str] = []
        seen: set[str] = set()
        attempts = 0
        while len(uniq) < n_per_class and attempts < n_per_class * 40:
            attempts += 1
            text = _fill(rng.choice(templates), rng)
            if text not in seen:
                seen.add(text)
                uniq.append(text)
        while len(uniq) < n_per_class:  # pad to keep classes balanced
            uniq.append(rng.choice(uniq))
        rows.extend((text, label) for text in uniq[:n_per_class])
    rng.shuffle(rows)
    return rows


def realistic_eval_set() -> list[tuple[str, str]]:
    """Hand-written, non-templated messages — the honest generalization test."""
    return [
        ("Hey, we're comparing vendors and need a ballpark price for ~50 seats", "pricing"),
        ("what's the damage per user per year?", "pricing"),
        ("Can someone walk me through the product on a quick call?", "demo_request"),
        ("Would love to see a live demo before we commit", "demo_request"),
        ("We're in — send me the contract and let's get going", "purchase_intent"),
        ("Take my money, how do I subscribe today", "purchase_intent"),
        ("Login's been throwing a 500 since this morning, pretty urgent", "support"),
        ("the export feature is busted for our whole team", "support"),
        ("Think there's a great integration play between our products", "partnership"),
        ("We run a community of 5k founders — open to a partnership?", "partnership"),
        ("saw a tweet about you, what do you actually do?", "general_inquiry"),
        ("just poking around, not sure if this is for us yet", "general_inquiry"),
        ("Boost your domain authority with 500 backlinks, special price", "spam"),
        ("I can flood your funnel with leads, check my site link", "spam"),
        ("Recent CS grad here, any openings for new engineers?", "job_inquiry"),
        ("attaching resume — would love to intern with your team", "job_inquiry"),
    ]
