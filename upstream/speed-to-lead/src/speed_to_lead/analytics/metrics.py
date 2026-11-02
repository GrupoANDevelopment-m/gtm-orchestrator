"""In-memory funnel metrics with source attribution.

The point isn't a fancy TSDB — it's to show the tool *thinks in funnels*:
speed-to-lead latency (the metric that drives conversion), qualification mix,
and where leads come from. Exposes a JSON snapshot and Prometheus text.
"""

from __future__ import annotations

import threading
from bisect import insort
from collections import Counter
from typing import cast

from ..models import FitTier, LeadOutcome


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    k = max(0, min(len(sorted_values) - 1, round((pct / 100) * (len(sorted_values) - 1))))
    return sorted_values[k]


class FunnelMetrics:
    """Thread-safe funnel counters. One per process (see `get_metrics`)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0
        self._by_tier: Counter[str] = Counter()
        self._by_source: Counter[str] = Counter()
        self._by_intent: Counter[str] = Counter()
        self._auto_sent = 0
        self._latencies_ms: list[float] = []  # kept sorted for percentiles

    def record(self, outcome: LeadOutcome) -> None:
        with self._lock:
            self._total += 1
            self._by_tier[outcome.qualification.tier.value] += 1
            self._by_source[outcome.lead.source] += 1
            self._by_intent[outcome.qualification.intent] += 1
            if outcome.routing.reply_sent:
                self._auto_sent += 1
            if outcome.latency_ms is not None:
                insort(self._latencies_ms, outcome.latency_ms)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            qualified = sum(self._by_tier[t.value] for t in (FitTier.HOT, FitTier.WARM))
            return {
                "leads_total": self._total,
                "qualification_rate": round(qualified / self._total, 3) if self._total else 0.0,
                "auto_send_rate": round(self._auto_sent / self._total, 3) if self._total else 0.0,
                "by_tier": dict(self._by_tier),
                "by_source": dict(self._by_source),  # attribution
                "by_intent": dict(self._by_intent),
                "response_ms_p50": round(_percentile(self._latencies_ms, 50), 1),
                "response_ms_p95": round(_percentile(self._latencies_ms, 95), 1),
            }

    def prometheus(self) -> str:
        """Minimal Prometheus exposition — scrapeable by the Grafana stack."""
        s = self.snapshot()
        lines = [
            "# HELP stl_leads_total Leads processed.",
            "# TYPE stl_leads_total counter",
            f"stl_leads_total {s['leads_total']}",
            "# HELP stl_qualification_rate Share of leads qualified (hot+warm).",
            "# TYPE stl_qualification_rate gauge",
            f"stl_qualification_rate {s['qualification_rate']}",
            "# HELP stl_response_ms Speed-to-lead latency.",
            "# TYPE stl_response_ms summary",
            f'stl_response_ms{{quantile="0.5"}} {s["response_ms_p50"]}',
            f'stl_response_ms{{quantile="0.95"}} {s["response_ms_p95"]}',
        ]
        by_tier = cast("dict[str, int]", s["by_tier"])
        for tier, n in by_tier.items():
            lines.append(f'stl_leads_by_tier{{tier="{tier}"}} {n}')
        return "\n".join(lines) + "\n"


_metrics = FunnelMetrics()


def get_metrics() -> FunnelMetrics:
    return _metrics
