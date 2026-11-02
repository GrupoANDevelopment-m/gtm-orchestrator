"""
DSH token meter adapter — REAL pattern ported from
deepseek-harness/packages/llm/llm-token-meter

DSH's token meter tracks:
- Tokens in / out per turn, per session, per model
- Cost in $ per session, per org
- Context window utilization
- Per-model breakdowns

We port that to SQLite-backed metering for our NIM calls. Every chat/agent
call records tokens_used (in/out) + cost, indexed by org_id for multi-tenant
billing.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path


METER_DB = Path(__file__).resolve().parent.parent / "data" / "dsh-meter.sqlite3"

# Cost per 1M tokens (NVIDIA NIM Nemotron 30B is roughly free tier; these are realistic)
COST_PER_MTOK = {
    "nvidia/nemotron-3.5-lightning-30b-a3b": {"in": 0.0, "out": 0.0},
    "stepfun-ai/step-3.7-flash": {"in": 0.0, "out": 0.0},
    "minimaxai/minimax-m3": {"in": 0.0, "out": 0.0},
}


def _init_db():
    METER_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(METER_DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS usage (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            session_id TEXT,
            model TEXT NOT NULL,
            tool TEXT,
            tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_usage_org ON usage(org_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_usage_session ON usage(session_id);
        CREATE INDEX IF NOT EXISTS idx_usage_model ON usage(model, created_at);
    """)
    conn.commit()
    conn.close()


_init_db()


def record(
    org_id: str,
    *,
    model: str,
    tokens_in: int,
    tokens_out: int,
    session_id: str | None = None,
    tool: str | None = None,
) -> dict:
    """Record one LLM call's token usage and cost.

    Mirrors llm-token-meter.recordUsage(): persists per-turn usage, computes
    cost from the model catalog, returns the entry id for reference.
    """
    costs = COST_PER_MTOK.get(model, {"in": 0.0, "out": 0.0})
    cost = (tokens_in / 1_000_000) * costs["in"] + (tokens_out / 1_000_000) * costs["out"]
    usage_id = f"usage-{uuid.uuid4().hex[:12]}"
    conn = sqlite3.connect(METER_DB)
    try:
        conn.execute(
            """INSERT INTO usage (id, org_id, session_id, model, tool, tokens_in,
               tokens_out, cost_usd, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (usage_id, org_id, session_id, model, tool, tokens_in, tokens_out, cost, time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": usage_id, "cost_usd": round(cost, 6), "tokens_in": tokens_in, "tokens_out": tokens_out}


def summary(org_id: str | None = None, since: float | None = None) -> dict:
    """Aggregate usage. Mirrors llm-token-meter.summarize()."""
    conn = sqlite3.connect(METER_DB)
    try:
        params: list = []
        where = []
        if org_id:
            where.append("org_id=?")
            params.append(org_id)
        if since:
            where.append("created_at>=?")
            params.append(since)
        sql = f"SELECT model, SUM(tokens_in), SUM(tokens_out), SUM(cost_usd), COUNT(*) FROM usage {('WHERE ' + ' AND '.join(where)) if where else ''} GROUP BY model"
        rows = conn.execute(sql, params).fetchall()
        per_model = {}
        total_in, total_out, total_cost = 0, 0, 0.0
        total_calls = 0
        for model, ti, to, cost, count in rows:
            per_model[model] = {
                "tokens_in": ti or 0,
                "tokens_out": to or 0,
                "cost_usd": round(cost or 0.0, 6),
                "calls": count,
            }
            total_in += ti or 0
            total_out += to or 0
            total_cost += cost or 0.0
            total_calls += count
        return {
            "org_id": org_id,
            "since": since,
            "totals": {
                "tokens_in": total_in,
                "tokens_out": total_out,
                "cost_usd": round(total_cost, 6),
                "calls": total_calls,
            },
            "per_model": per_model,
        }
    finally:
        conn.close()


def session_breakdown(session_id: str) -> dict:
    """Per-session usage breakdown (DSH: session-level stats)."""
    conn = sqlite3.connect(METER_DB)
    try:
        rows = conn.execute(
            "SELECT tool, model, SUM(tokens_in), SUM(tokens_out), SUM(cost_usd) FROM usage WHERE session_id=? GROUP BY tool, model",
            (session_id,),
        ).fetchall()
        items = [
            {"tool": t, "model": m, "tokens_in": ti or 0, "tokens_out": to or 0, "cost_usd": round(c or 0.0, 6)}
            for t, m, ti, to, c in rows
        ]
        return {"session_id": session_id, "items": items, "count": len(items)}
    finally:
        conn.close()


SOURCE = "REAL DSH llm-token-meter pattern (deepseek-harness/packages/llm/llm-token-meter) — per-org cost tracking, per-model breakdown, session-level stats"
