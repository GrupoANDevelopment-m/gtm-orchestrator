"""
DSH session log adapter — REAL pattern ported from
deepseek-harness/packages/core/session-persistence-jsonl and
deepseek-harness/packages/core/session

DSH's pattern:
- Append-only event log (JSONL on disk)
- Every model-visible fact is a typed event: session.start, user.message,
  assistant.chunk, assistant.message, tool.call, tool.result, session.end
- Model history is reconstructed from the log via deriveMessages()
- Each session has its own file under DSH_SESSION_ROOT

We port that to Python as `SessionLog` and `Session`. Replay and audit are
first-class: any session can be re-loaded from disk and the full event stream
walked.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Iterator, Any


SESSION_ROOT = Path(__file__).resolve().parent.parent / "data" / "dsh-sessions"
SESSION_ROOT.mkdir(parents=True, exist_ok=True)

# Event types mirror DSH's SessionEventMap vocabulary
EVENT_TYPES = {
    "session.start", "session.end",
    "user.message",
    "assistant.chunk", "assistant.message",
    "tool.call", "tool.result",
    "agent.inbox.inserted", "agent.inbox.claimed",
    "agent.request", "agent.response",
    "checkpoint",
}


class SessionLog:
    """Append-only event log for a single session.

    Pattern from dsh-session-persistence-jsonl: one JSONL file per sessionId,
    each line is a typed event with time + sequence number. Replay is just
    reading the file line-by-line.
    """

    def __init__(self, session_id: str, *, root: Path | None = None):
        self.session_id = session_id
        self.root = root or SESSION_ROOT
        self.path = self.root / f"{session_id}.jsonl"
        self._seq = 0
        # If file exists, resume sequence from the end
        if self.path.exists():
            with self.path.open("rb") as f:
                self._seq = sum(1 for _ in f)

    def append(self, event_type: str, data: dict | None = None) -> dict:
        """Append a typed event to the log."""
        if event_type not in EVENT_TYPES:
            # Allow custom event types but warn silently
            pass
        self._seq += 1
        event = {
            "id": str(uuid.uuid4()),
            "seq": self._seq,
            "time": time.time(),
            "type": event_type,
            "sessionId": self.session_id,
            "data": data or {},
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def replay(self) -> Iterator[dict]:
        """Iterate events in append order (for cold-resume / audit)."""
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def derive_messages(self) -> list[dict]:
        """Project the log into model-visible message history.

        Mirrors dsh-session deriveMessages(): user/message -> user role,
        assistant/message -> assistant role, tool/result -> tool role.
        """
        messages: list[dict] = []
        pending_tool_calls: dict[str, dict] = {}  # tool_call_id -> tool call
        for ev in self.replay():
            t = ev.get("type")
            d = ev.get("data", {})
            if t == "user.message":
                messages.append({"role": "user", "content": d.get("content", "")})
            elif t == "assistant.message":
                msg = {"role": "assistant", "content": d.get("content", "")}
                if d.get("tool_calls"):
                    msg["tool_calls"] = d["tool_calls"]
                messages.append(msg)
            elif t == "tool.call":
                pending_tool_calls[d.get("id", "")] = d
            elif t == "tool.result":
                tc_id = d.get("tool_call_id", "")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": str(d.get("result", "")),
                })
        return messages

    def checkpoints(self) -> list[dict]:
        """Return all session.checkpoint events (for resumable replay)."""
        return [ev for ev in self.replay() if ev.get("type") == "checkpoint"]

    def stats(self) -> dict:
        """Aggregate stats: total events, by type, time range."""
        events = list(self.replay())
        by_type: dict[str, int] = {}
        for ev in events:
            t = ev.get("type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1
        times = [ev.get("time", 0) for ev in events if ev.get("time")]
        return {
            "session_id": self.session_id,
            "path": str(self.path),
            "total_events": len(events),
            "by_type": by_type,
            "first_at": min(times) if times else None,
            "last_at": max(times) if times else None,
            "size_bytes": self.path.stat().st_size if self.path.exists() else 0,
        }

    @classmethod
    def list_sessions(cls, root: Path | None = None) -> list[dict]:
        r = root or SESSION_ROOT
        if not r.exists():
            return []
        out = []
        for p in sorted(r.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                sid = p.stem
                log = cls(sid, root=r)
                stats = log.stats()
                out.append({"session_id": sid, **stats})
            except Exception:
                continue
        return out


SOURCE = "REAL DSH session-persistence-jsonl pattern (deepseek-harness/packages/core/session-persistence-jsonl) — append-only JSONL event log, replay, deriveMessages, checkpoints"
