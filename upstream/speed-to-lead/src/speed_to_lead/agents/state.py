"""Shared state for the LangGraph pipeline."""

from __future__ import annotations

from typing import TypedDict

from ..models import DraftResult, EnrichmentResult, Lead, QualificationResult, RouteResult


class PipelineState(TypedDict, total=False):
    """Accumulating state passed node-to-node through the graph."""

    lead: Lead
    enrichment: EnrichmentResult
    qualification: QualificationResult
    draft: DraftResult
    routing: RouteResult
