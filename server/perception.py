"""
Perception Layer (Layer 1) — unified input adapter.

Sources:
- User input (chat, voice transcribed)
- APIs (webhooks, REST)
- Web (Playwright, Jina, Agent-Reach)
- Sensors (timing, queue depth, system metrics)
- News/RSS (Agent-Reach channels)
- Databases (CRM leads, deals)
- Osiris (OSINT/Geo) — Google Maps via Playwright
- External agents (subagent results)
- MCP servers (placeholder; full MCP needs official SDK)
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Percept:
    """A single perception unit — the smallest input the loop reasons about."""
    id: str = field(default_factory=lambda: f"perc-{uuid.uuid4().hex[:12]}")
    source: str = "user"           # user|api|web|sensor|news|database|osiris|agent|mcp
    modality: str = "text"          # text|json|file|url|metric|event
    content: Any = None
    confidence: float = 1.0
    provenance: str = ""
    captured_at: float = field(default_factory=time.time)
    session_id: str | None = None
    org_id: str = "default"
    tags: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "source": self.source, "modality": self.modality,
            "content": self.content, "confidence": self.confidence,
            "provenance": self.provenance, "captured_at": self.captured_at,
            "session_id": self.session_id, "org_id": self.org_id, "tags": self.tags,
        }


# === Source adapters ===

def from_user(text: str, *, session_id: str | None = None, org_id: str = "default") -> Percept:
    return Percept(source="user", modality="text", content=text,
                   session_id=session_id, org_id=org_id, tags=["chat-input"])


def from_api(payload: dict, *, source: str = "webhook",
             session_id: str | None = None, org_id: str = "default") -> Percept:
    return Percept(source="api", modality="json", content=payload,
                   session_id=session_id, org_id=org_id,
                   tags=[source], provenance=source)


def from_web(url: str, content: str | None = None, *,
             session_id: str | None = None, org_id: str = "default") -> Percept:
    return Percept(source="web", modality="url", content={"url": url, "excerpt": (content or "")[:2000]},
                   session_id=session_id, org_id=org_id, tags=["fetched"])


def from_database(entity: str, rows: list, *, org_id: str = "default",
                  session_id: str | None = None) -> Percept:
    return Percept(source="database", modality="json",
                   content={"entity": entity, "count": len(rows), "sample": rows[:10]},
                   session_id=session_id, org_id=org_id, tags=[f"db:{entity}"])


def from_sensor(metric: str, value: float, *, org_id: str = "default") -> Percept:
    return Percept(source="sensor", modality="metric", content={"metric": metric, "value": value},
                   org_id=org_id, tags=["runtime"])


def from_osiris(target: str, geo: dict, *, session_id: str | None = None,
                org_id: str = "default") -> Percept:
    """OSINT/Geo perception — from Google Maps or other geo sources."""
    return Percept(source="osiris", modality="json", content={"target": target, "geo": geo},
                   session_id=session_id, org_id=org_id,
                   tags=["osint", "geo", f"target:{target}"])


def from_agent(name: str, output: Any, *, session_id: str | None = None,
               org_id: str = "default") -> Percept:
    return Percept(source="agent", modality="json", content={"agent": name, "output": output},
                   session_id=session_id, org_id=org_id, tags=[f"agent:{name}"])


def from_mcp(server: str, tool: str, result: Any, *, org_id: str = "default") -> Percept:
    """Placeholder for Model Context Protocol input. Full MCP requires official SDK;
    we expose the shape so future MCP servers plug in."""
    return Percept(source="mcp", modality="json",
                   content={"server": server, "tool": tool, "result": result},
                   org_id=org_id, tags=["mcp", f"server:{server}"])


def from_news(channel: str, item: dict, *, org_id: str = "default") -> Percept:
    return Percept(source="news", modality="json", content=item,
                   org_id=org_id, tags=[f"channel:{channel}"])


# === Perception batch (one cycle of perception) ===

@dataclass
class PerceptionBatch:
    session_id: str
    percepts: list = field(default_factory=list)
    captured_at: float = field(default_factory=time.time)

    def add(self, percept: Percept) -> None:
        self.percepts.append(percept)

    def sources(self) -> dict:
        out: dict = {}
        for p in self.percepts:
            out[p.source] = out.get(p.source, 0) + 1
        return out

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "captured_at": self.captured_at,
            "count": len(self.percepts),
            "sources": self.sources(),
            "percepts": [p.to_dict() for p in self.percepts],
        }


SOURCE = "Layer 1 PERCEPTION — unified input adapter for user|api|web|sensor|news|database|osiris|agent|mcp"
