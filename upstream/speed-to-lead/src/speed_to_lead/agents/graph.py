"""The lead pipeline as a LangGraph state machine.

    research → qualify → ┬─ (spam) ─────────────→ discard → END
                         └─ (real) → draft → route ────────→ END

Each node is a small, testable async function. Services are injected at build
time so the graph is provider-agnostic and easy to unit-test with fakes.
"""

from __future__ import annotations

import time

from langgraph.graph import END, START, StateGraph

from ..config import Settings, get_settings
from ..integrations.crm import get_crm
from ..integrations.notify import get_notifier
from ..logging import get_logger
from ..models import DraftResult, FitTier, Lead, LeadOutcome, RouteResult
from ..services.draft import get_drafter
from ..services.enrich import get_enricher
from ..services.icp import IcpIndex
from ..services.qualify import Qualifier, get_qualifier
from .state import PipelineState

log = get_logger(__name__)


class LeadPipeline:
    """Compiled LangGraph pipeline with injected, swappable services."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        qualifier: Qualifier | None = None,
        icp: IcpIndex | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._enricher = get_enricher()
        self._qualifier = qualifier or get_qualifier()
        self._icp = icp
        self._drafter = get_drafter(self.settings)
        self._crm = get_crm(self.settings)
        self._notifier = get_notifier(self.settings)
        self._graph = self._build()

    # --- nodes -----------------------------------------------------------
    async def _research(self, state: PipelineState) -> PipelineState:
        lead = state["lead"]
        enrichment = await self._enricher.enrich(lead)
        if self._icp is not None:
            text = f"{lead.company or ''} {lead.message or ''}".strip()
            enrichment.icp_similarity = self._icp.similarity(text)
        return {"enrichment": enrichment}

    async def _qualify(self, state: PipelineState) -> PipelineState:
        qual = self._qualifier.qualify(state["lead"], state["enrichment"])
        log.info("qualify", email=state["lead"].email, tier=qual.tier.value, score=qual.score)
        return {"qualification": qual}

    async def _draft(self, state: PipelineState) -> PipelineState:
        qual = state["qualification"]
        draft = await self._drafter.draft(state["lead"], state["enrichment"], qual)
        # Confidence gate: only auto-send when the model is sure and the lead is worth it.
        draft.requires_review = (
            qual.confidence < self.settings.auto_send_min_confidence
            or qual.tier not in (FitTier.HOT, FitTier.WARM)
        )
        return {"draft": draft}

    async def _route(self, state: PipelineState) -> PipelineState:
        lead, qual, draft = state["lead"], state["qualification"], state["draft"]
        crm_id = await self._crm.upsert_person(lead, qual)
        notified = await self._notifier.notify(lead, qual, draft)
        reply_sent = not draft.requires_review
        return {
            "routing": RouteResult(
                crm_id=crm_id,
                crm_provider=self._crm.provider,
                notified=notified,
                reply_sent=reply_sent,
            )
        }

    async def _discard(self, state: PipelineState) -> PipelineState:
        lead, qual = state["lead"], state["qualification"]
        crm_id = await self._crm.upsert_person(lead, qual)  # still record it, marked spam
        return {"routing": RouteResult(crm_id=crm_id, crm_provider=self._crm.provider)}

    @staticmethod
    def _branch(state: PipelineState) -> str:
        return "discard" if state["qualification"].tier is FitTier.SPAM else "draft"

    # --- assembly --------------------------------------------------------
    def _build(self) -> object:
        g = StateGraph(PipelineState)
        g.add_node("research", self._research)
        g.add_node("qualify", self._qualify)
        g.add_node("draft", self._draft)
        g.add_node("route", self._route)
        g.add_node("discard", self._discard)

        g.add_edge(START, "research")
        g.add_edge("research", "qualify")
        g.add_conditional_edges("qualify", self._branch, {"draft": "draft", "discard": "discard"})
        g.add_edge("draft", "route")
        g.add_edge("route", END)
        g.add_edge("discard", END)
        return g.compile()

    # --- public API ------------------------------------------------------
    async def run(self, lead: Lead) -> LeadOutcome:
        """Run one lead through the full graph and return the audit-friendly outcome."""
        start = time.perf_counter()
        final: PipelineState = await self._graph.ainvoke({"lead": lead})  # type: ignore[attr-defined]
        latency_ms = (time.perf_counter() - start) * 1000
        return LeadOutcome(
            lead=final["lead"],
            enrichment=final["enrichment"],
            qualification=final["qualification"],
            draft=final.get("draft") or _spam_placeholder_draft(),
            routing=final["routing"],
            latency_ms=round(latency_ms, 2),
        )


def _spam_placeholder_draft() -> DraftResult:
    return DraftResult(
        subject="(no reply — filtered)", body="", channel="none", requires_review=True, model="none"
    )


def build_pipeline(
    settings: Settings | None = None,
    *,
    qualifier: Qualifier | None = None,
    icp: IcpIndex | None = None,
) -> LeadPipeline:
    return LeadPipeline(settings, qualifier=qualifier, icp=icp)
