"""
Reasoning Engine (Layer 4) — chain-of-thought, planning, decomposition,
simulation, hypothesis generation.

The reasoning engine takes a goal + world model and produces N candidate
hypotheses (candidate action plans). Each hypothesis is a list of tool calls
the loop could execute. The evidence evaluator (Layer 6) then judges which one
to actually run.

Approach:
1. Decompose goal into sub-tasks
2. For each sub-task, generate 1-3 candidate action plans
3. Simulate each plan against the world model (cheap preview)
4. Return ranked hypotheses

We use the LLM (NIM) to do the heavy lifting, with a deterministic fallback
when no key.
"""
from __future__ import annotations

import json
import time
from typing import Any

from llm_adapter import _load_key, DEFAULT_MODEL, _nim_chat


SYSTEM_PROMPT_REASONER = """You are the reasoning engine of a GTM orchestrator.
Given a goal + world model snapshot + available tools, decompose the goal
into sub-tasks and generate 2-4 candidate action plans.

For each hypothesis, return JSON with this exact shape:
{
  "decomposition": [
    {"step": "step description", "rationale": "why"}
  ],
  "hypotheses": [
    {
      "id": "H1",
      "summary": "one-line plan",
      "actions": [
        {"tool": "tool_name", "args": {...}, "rationale": "why this"}
      ],
      "predicted_outcome": "what you expect to happen",
      "predicted_cost_ms": 5000,
      "confidence": 0.0-1.0
    }
  ],
  "best_choice": "H1"
}

Only return JSON. No prose, no markdown."""


def reason(goal: dict, world_model: dict, tools_catalog: list[dict],
           *, model: str = None, nim_key: str | None = None) -> dict:
    """Generate ranked hypotheses for a goal given current world model.
    Uses NIM when key available, falls back to deterministic stub.
    """
    prompt = json.dumps({
        "goal": goal,
        "world_model_summary": {
            "session_id": world_model.get("session_id"),
            "active_goal": world_model.get("active_goal"),
            "last_action": world_model.get("last_action"),
            "lesson_bank": world_model.get("lesson_bank", [])[:5],
            "recent_episodes_count": len(world_model.get("recent_episodes", [])),
            "capability_catalog_size": world_model.get("capability_catalog_size"),
        },
        "available_tools": [{"name": t["function"]["name"],
                             "description": t["function"].get("description", "")[:120]}
                            for t in tools_catalog],
    }, indent=2, default=str)

    # Try NIM
    key = nim_key or _load_key()
    if key:
        try:
            model_id = model or DEFAULT_MODEL
            content = _nim_chat(SYSTEM_PROMPT_REASONER, prompt, model=model_id)
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                content = "\n".join(lines).strip()
            return {"source": "nim_reasoner", "model": model_id, "plan": json.loads(content)}
        except Exception as e:
            return {"source": "nim_reasoner_fallback", "error": str(e),
                    "plan": _deterministic_plan(goal, tools_catalog)}

    # Fallback: deterministic plan generation
    return {"source": "deterministic_stub", "plan": _deterministic_plan(goal, tools_catalog)}


def _deterministic_plan(goal: dict, tools_catalog: list[dict]) -> dict:
    """When no NIM key: build a simple 1-hypothesis plan from keyword matching."""
    desc = (goal.get("description") or "").lower()
    tool_names = {t["function"]["name"] for t in tools_catalog}
    chosen: list[dict] = []

    if any(k in desc for k in ["prospect", "find", "search", "discover", "lead"]):
        if "google_maps_prospect" in tool_names:
            chosen.append({"tool": "google_maps_prospect", "args": {"query": "SaaS startup", "city": "Berlin", "count": 5}, "rationale": "match keyword prospect"})
    if any(k in desc for k in ["score", "qualify", "icp"]):
        if "qualify_inbound" in tool_names:
            chosen.append({"tool": "qualify_inbound", "args": {"message": "demo request"}, "rationale": "match qualify"})
    if any(k in desc for k in ["draft", "email", "outbound", "write"]):
        if "draft_outbound" in tool_names:
            chosen.append({"tool": "draft_outbound", "args": {"contact_name": "Sarah", "company_name": "ScaleUp", "industry": "SaaS"}, "rationale": "match draft"})
    if not chosen and "list_leads" in tool_names:
        chosen.append({"tool": "list_leads", "args": {}, "rationale": "default fallback"})

    return {
        "decomposition": [{"step": "execute chosen tools sequentially", "rationale": "deterministic"}],
        "hypotheses": [{
            "id": "H1",
            "summary": "Deterministic fallback plan",
            "actions": chosen,
            "predicted_outcome": "list/prospect leads successfully",
            "predicted_cost_ms": 5000,
            "confidence": 0.4,
        }],
        "best_choice": "H1",
    }


SOURCE = "Layer 4 REASONING ENGINE — NIM-powered hypothesis generation + deterministic fallback"
