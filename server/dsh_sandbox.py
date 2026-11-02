"""
DSH sandbox/guard adapter — REAL pattern ported from
deepseek-harness/packages/sandbox/sandbox-policy and
deepseek-harness/packages/guard/{repeat-tool-reminder,timeout-policy}

DSH's capability seams:
- sandbox policy: explicit allow/deny for tool inputs
- guard policies: timeouts per tool, repeat-call reminders
- approval: 'never' for delegated children, 'ask' otherwise

We port this with a simple allow/deny policy + per-tool timeout + repeat
reminder. The orchestrator wraps every tool call through the guard.
"""
from __future__ import annotations

import functools
import time
from typing import Any, Callable


# Default policy: most tools allowed with reasonable timeouts
DEFAULT_POLICY = {
    # tool name -> { timeout_seconds, max_calls_per_minute, requires_approval }
    "google_maps_prospect": {"timeout_seconds": 60, "max_calls_per_minute": 10, "approval": False},
    "agent_reach_read":     {"timeout_seconds": 30, "max_calls_per_minute": 20, "approval": False},
    "extract_leads_from_url": {"timeout_seconds": 45, "max_calls_per_minute": 10, "approval": False},
    "send_email":           {"timeout_seconds": 15, "max_calls_per_minute": 60, "approval": True},
    "send_whatsapp":        {"timeout_seconds": 15, "max_calls_per_minute": 60, "approval": True},
    "enrich_lead":          {"timeout_seconds": 20, "max_calls_per_minute": 30, "approval": False},
    "qualify_inbound":      {"timeout_seconds": 5,  "max_calls_per_minute": 100, "approval": False},
    "score_lead_icp":       {"timeout_seconds": 15, "max_calls_per_minute": 60, "approval": False},
    "score_deal_risk":      {"timeout_seconds": 15, "max_calls_per_minute": 60, "approval": False},
    "score_account_health": {"timeout_seconds": 15, "max_calls_per_minute": 60, "approval": False},
    "draft_outbound":       {"timeout_seconds": 30, "max_calls_per_minute": 60, "approval": False},
    "draft_reply":          {"timeout_seconds": 30, "max_calls_per_minute": 60, "approval": False},
    "fetch_url":            {"timeout_seconds": 20, "max_calls_per_minute": 30, "approval": False},
}


class ToolGuard:
    """Real DSH guard pattern — every tool call goes through policy check
    (sandbox/timeout/rate-limit) + repeat reminder after N consecutive uses.
    """

    def __init__(self, *, policy: dict | None = None):
        self.policy = policy or DEFAULT_POLICY
        self._call_log: list[tuple[str, float]] = []  # (tool, time)
        self._repeat_count: dict[str, int] = {}

    def check_rate_limit(self, tool: str) -> dict:
        """Mirror of dsh-sandbox-policy rate limit check."""
        now = time.time()
        # Drop entries older than 60s
        self._call_log = [(t, ts) for t, ts in self._call_log if now - ts < 60]
        recent = sum(1 for t, _ in self._call_log if t == tool)
        max_per_min = self.policy.get(tool, {}).get("max_calls_per_minute", 100)
        allowed = recent < max_per_min
        return {"allowed": allowed, "recent": recent, "max": max_per_min, "retry_after_seconds": 60 if not allowed else 0}

    def check_approval(self, tool: str) -> bool:
        """Mirror of dsh-approval-policy. True = needs human approval."""
        return self.policy.get(tool, {}).get("approval", False)

    def repeat_reminder(self, tool: str) -> str | None:
        """Mirror of dsh-guard/repeat-tool-reminder: after N consecutive calls,
        inject a reminder message asking the model to consider another approach."""
        self._repeat_count[tool] = self._repeat_count.get(tool, 0) + 1
        if self._repeat_count[tool] >= 3:
            # Reset so we don't spam; emit one reminder every 3 calls
            self._repeat_count[tool] = 0
            return (
                f"You've called `{tool}` {self._repeat_count[tool] + 3} times in a row. "
                "Consider whether a different tool or a direct answer would serve the user better."
            )
        return None

    def enforce(self, tool: str, fn: Callable, *args, **kwargs) -> Any:
        """Wrap a tool call with rate-limit, approval, and timeout enforcement.

        Pattern from dsh-sandbox/sandbox-local + dsh-guard/timeout-policy:
        - rate-limit pre-check
        - approval gate (returns sentinel; caller must check)
        - timeout via signal-style wrapper (sync version)
        - repeat reminder injected into result if applicable
        """
        rl = self.check_rate_limit(tool)
        if not rl["allowed"]:
            return {
                "ok": False,
                "error": "rate_limited",
                "retry_after_seconds": rl["retry_after_seconds"],
                "tool": tool,
            }
        if self.check_approval(tool):
            return {"ok": False, "requires_approval": True, "tool": tool}

        # Record the call
        self._call_log.append((tool, time.time()))

        # Execute with timeout (Python signal-based, sync only)
        timeout = self.policy.get(tool, {}).get("timeout_seconds", 30)
        import signal

        def _timeout_handler(signum, frame):
            raise TimeoutError(f"tool '{tool}' exceeded {timeout}s timeout")

        prev_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout)
        try:
            result = fn(*args, **kwargs)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, prev_handler)

        reminder = self.repeat_reminder(tool)
        if reminder:
            if isinstance(result, dict):
                result.setdefault("_reminders", []).append(reminder)
            else:
                result = {"value": result, "_reminders": [reminder]}
        return result


# Singleton
_guard: ToolGuard | None = None


def get_guard() -> ToolGuard:
    global _guard
    if _guard is None:
        _guard = ToolGuard()
    return _guard


SOURCE = "REAL DSH sandbox-policy + guard pattern (deepseek-harness/packages/sandbox + guard) — rate-limit, approval gate, per-tool timeout, repeat-call reminder"
