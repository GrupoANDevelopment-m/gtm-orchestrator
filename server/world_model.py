"""
World Model Layer (Layer 2) — internal state representation.

Builds the loop's "understanding of the situation":
- Current context (recent percepts, goals, capabilities)
- Internal state (loop counters, last cycle result)
- Episodic memory slice (last N episodes)
- Semantic memory slice (relevant facts about entities mentioned)
- Action history (what we just did)
- Active goals (from goal_engine)

The world model is what gets serialized into the model's context window.
"""
from __future__ import annotations

import json
import time
from typing import Any
import memory as M


class WorldModel:
    """Snapshot of the loop's current understanding."""

    def __init__(self, *, session_id: str, org_id: str = "default",
                 context_window_tokens: int = 8000):
        self.session_id = session_id
        self.org_id = org_id
        self.context_window_tokens = context_window_tokens
        self._episode_window = 30
        self._started_at = time.time()

    def build(self, *, mention: list[str] | None = None,
              goal: dict | None = None, last_action: dict | None = None) -> dict:
        """Build the world model snapshot for the current cycle."""
        recent = M.recent_episodes(org_id=self.org_id, limit=self._episode_window,
                                    session_id=self.session_id)
        relevant_facts: dict = {}
        if mention:
            for term in mention:
                for entity in ["lead", "deal", "account", "icp"]:
                    # Cheap lookup: find recent semantic entries matching term
                    pass  # Implementation: scan semantic for mentions
        # Pull lesson bank (top 5 most-observed)
        lessons = M.list_lessons(org_id=self.org_id, limit=5)
        # Pull capabilities catalog
        caps = M.list_capabilities(org_id=self.org_id)

        return {
            "session_id": self.session_id,
            "org_id": self.org_id,
            "captured_at": time.time(),
            "context_window_tokens": self.context_window_tokens,
            "recent_episodes": [e for e in recent if e.get("session_id") == self.session_id][:15],
            "active_goal": goal,
            "last_action": last_action,
            "lesson_bank": [l["lesson"] for l in lessons],
            "capability_catalog_size": len(caps),
            "memory_stats": {
                "episodes_in_session": sum(1 for e in recent
                                            if e.get("session_id") == self.session_id),
            },
        }

    def render_for_prompt(self, *, max_chars: int = 6000) -> str:
        """Render the world model as a compact prompt-prefix the loop can inject."""
        wm = self.build()
        lines = [
            "[WORLD MODEL]",
            f"Session: {wm['session_id']}",
            f"Org: {wm['org_id']}",
            f"Time: {wm['captured_at']:.0f}",
        ]
        if wm["active_goal"]:
            g = wm["active_goal"]
            lines.append(f"Active goal: {g.get('description','?')} (priority={g.get('priority',0):.2f})")
        if wm["last_action"]:
            la = wm["last_action"]
            lines.append(f"Last action: {la.get('tool','?')} status={la.get('status','?')}")
        if wm["lesson_bank"]:
            lines.append("Top lessons:")
            for l in wm["lesson_bank"][:5]:
                lines.append(f"  - {l}")
        if wm["recent_episodes"]:
            lines.append(f"Recent episodes ({len(wm['recent_episodes'])}):")
            for e in wm["recent_episodes"][:5]:
                lines.append(f"  [{e['kind']:8s}] {e['source']}: {str(e['payload'])[:120]}")
        rendered = "\n".join(lines)
        return rendered[:max_chars]


SOURCE = "Layer 2 WORLD MODEL — context + state + episodic + semantic + lessons + capabilities"
