"""MCP server exposing lead qualification + drafting as tools.

Lets an MCP client (Claude Desktop, Cursor, …) qualify a lead or draft a reply
using the exact same services the API and graph use. Run: `speed-to-lead-mcp`.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..config import get_settings
from ..models import InboundLead
from ..normalize import normalize_lead
from ..services.draft import get_drafter
from ..services.enrich import get_enricher
from ..services.qualify import get_qualifier

mcp = FastMCP("speed-to-lead-agent")
_enricher = get_enricher()
_qualifier = get_qualifier()


@mcp.tool()
async def qualify_lead(email: str, message: str = "", company: str | None = None) -> dict[str, Any]:
    """Qualify an inbound lead. Returns tier, score, intent, and the reasons behind them."""
    lead = normalize_lead(InboundLead(email=email, message=message, company=company))
    enrichment = await _enricher.enrich(lead)
    return _qualifier.qualify(lead, enrichment).model_dump()


@mcp.tool()
async def draft_reply(email: str, message: str = "", company: str | None = None) -> dict[str, Any]:
    """Qualify a lead, then draft a personalized first-touch reply for it."""
    lead = normalize_lead(InboundLead(email=email, message=message, company=company))
    enrichment = await _enricher.enrich(lead)
    qual = _qualifier.qualify(lead, enrichment)
    draft = await get_drafter(get_settings()).draft(lead, enrichment, qual)
    return {"qualification": qual.model_dump(), "draft": draft.model_dump()}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
