"""
DSH skills adapter — REAL pattern ported from
deepseek-harness/packages/skill

DSH's skill system:
- Skills are loadable tool bundles defined in markdown files
- Each skill has a name, description, when-to-use hint, and tool registrations
- Skills can be invoked via the `skill` tool which loads on-demand
- DSH supports both built-in skills (shipped with plugins) and user-loaded ones

We port this as a markdown-driven skill loader. Skills live in
`upstream/dsh-skills/*.md` and define:
- name, description
- tools to register
- example invocations
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

SKILLS_DIR = Path(__file__).resolve().parent.parent / "upstream" / "dsh-skills"


SKILL_MD_TEMPLATE = """# {name}

{description}

## When to use
{when_to_use}

## Tools
{tools}

## Example
```
{example}
```
"""


def parse_skill_md(path: Path) -> dict:
    """Parse a DSH-style skill markdown file."""
    text = path.read_text(encoding="utf-8")
    name_match = re.match(r"#\s+(\S+)", text)
    name = name_match.group(1) if name_match else path.stem
    sections = re.split(r"\n##\s+", text)
    parsed = {"path": str(path), "name": name, "raw": text}
    for sec in sections[1:]:
        lines = sec.strip().split("\n", 1)
        if not lines:
            continue
        key = lines[0].strip().lower().replace(" ", "_")
        val = lines[1].strip() if len(lines) > 1 else ""
        parsed[key] = val
    return parsed


def list_skills(directory: Path | None = None) -> list[dict]:
    d = directory or SKILLS_DIR
    if not d.exists():
        return []
    return [parse_skill_md(p) for p in d.glob("*.md")]


def create_skill(name: str, description: str, when_to_use: str, tools: list[str], example: str = "") -> dict:
    """Create a new skill (writes the markdown to disk)."""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    path = SKILLS_DIR / f"{name.lower().replace(' ', '_')}.md"
    body = SKILL_MD_TEMPLATE.format(
        name=name,
        description=description,
        when_to_use=when_to_use,
        tools="\n".join(f"- `{t}`" for t in tools),
        example=example or "see docs",
    )
    path.write_text(body, encoding="utf-8")
    return {"name": name, "path": str(path), "tools": tools}


def invoke_skill(name: str, inputs: dict[str, Any], *, registry: dict[str, Callable] | None = None) -> dict:
    """Invoke a skill by name — looks up tools in registry and runs them."""
    skill = None
    for s in list_skills():
        if s.get("name", "").lower() == name.lower():
            skill = s
            break
    if not skill:
        return {"ok": False, "error": f"skill '{name}' not found"}
    tool_line = skill.get("tools", "")
    tool_names = re.findall(r"`(\w+)`", tool_line)
    if registry:
        results = {}
        for tn in tool_names:
            fn = registry.get(tn)
            if fn:
                try:
                    results[tn] = fn(**inputs)
                except Exception as e:
                    results[tn] = {"error": f"{type(e).__name__}: {e}"}
            else:
                results[tn] = {"error": "tool not in registry"}
        return {"ok": True, "skill": name, "results": results}
    return {"ok": True, "skill": name, "tools_available": tool_names, "registry_required": True}


SOURCE = "REAL DSH skill pattern (deepseek-harness/packages/skill) — markdown-defined skill bundles, on-demand loading, tool registration"


# Seed a few canonical skills on first run
_DEFAULT_SKILLS = [
    {
        "name": "prospect-batch",
        "description": "Prospect multiple cities in parallel using Google Maps.",
        "when_to_use": "User asks to find leads in N cities, e.g. 'Find SaaS startups in Miami, Austin, Boston'.",
        "tools": ["google_maps_prospect", "prospex_score_batch"],
        "example": "POST /api/subagent/delegate {provider: 'prospect_city', specs: [{name:'Miami', input:{query:'SaaS startup',city:'Miami'}}, ...]}",
    },
    {
        "name": "speed-to-lead-full",
        "description": "End-to-end inbound lead handling: qualify + score + draft + enqueue.",
        "when_to_use": "Inbound webhook fires (form, Twilio, email parser).",
        "tools": ["qualify_inbound", "score_lead_icp", "draft_reply"],
        "example": "POST /webhooks/inbound {email, message} -> pipeline auto-runs",
    },
    {
        "name": "competitive-intel",
        "description": "Read competitor presence on 5+ platforms in parallel and summarize.",
        "when_to_use": "User asks about competitor activity, e.g. 'What's Stripe shipping this week?'",
        "tools": ["agent_reach_read", "fetch_url"],
        "example": "agent_reach_read platform=twitter target=stripe; agent_reach_read platform=github target=stripe; ...",
    },
]


def seed_default_skills():
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    existing = {p.stem for p in SKILLS_DIR.glob("*.md")}
    for s in _DEFAULT_SKILLS:
        key = s["name"].lower().replace(" ", "-")
        if key in existing:
            continue
        create_skill(s["name"], s["description"], s["when_to_use"], s["tools"], s["example"])
