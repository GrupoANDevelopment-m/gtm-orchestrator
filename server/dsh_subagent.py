"""
DSH subagent adapter — REAL pattern ported from
deepseek-harness/packages/subagent/subagent/README.md

DSH's SubagentRuntime:
- One-shot children: registerProvider, start(name, request) -> SubagentRun
- Continuable children: startContinuable(spec), followup(parent, childId, ...)
- Delegation depth tracking (monotone floor via SessionHeader.delegationDepth)
- Subagent policy inheritance: child inherits parent's sandbox + 'never' approval
- One-shot fulfillment: provider owns pre-publication resources, caller owns
  post-publication via dispose()
- Result contract: { output, structured?, diagnostic?, stopReason }

We port this as a parallel execution runner with policy inheritance.
`delegate(name, tasks, ctx)` runs N tasks concurrently with shared parent policy,
returns when all settle (similar to SubagentRun.result).
"""
from __future__ import annotations

import asyncio
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


@dataclass
class DelegatedPolicy:
    """Mirrors DSH's captureDelegatedPolicyOverrides — parent's policy snapshot
    that the child inherits (sandbox scope + approval='never' for delegated children)."""
    sandbox: str = "default"
    approval: str = "never"
    tools: tuple[str, ...] = ()
    depth: int = 0
    max_depth: int = 3


@dataclass
class SubagentRun:
    """Mirrors DSH's SubagentRun contract."""
    id: str
    parent_id: str
    name: str
    started_at: float
    finished_at: float | None = None
    status: str = "running"  # running | completed | aborted | rejected
    output: list[str] = field(default_factory=list)
    structured: Any = None
    diagnostic: str | None = None
    stop_reason: str | None = None

    def settle(self, *, output: list[str], structured: Any = None, stop_reason: str = "completed"):
        self.finished_at = time.time()
        self.output = output
        self.structured = structured
        self.stop_reason = stop_reason
        self.status = "completed"


@dataclass
class SubagentSpec:
    """Spec for one parallel task — like DSH's one-shot request."""
    name: str
    description: str
    input: Any
    tools: tuple[str, ...] = ()


class SubagentRuntime:
    """REAL DSH SubagentRuntime pattern — parallel delegation with policy
    inheritance. The orchestrator uses this for parallel prospect tasks."""

    def __init__(self, *, max_depth: int = 3, max_concurrent: int = 8):
        self._providers: dict[str, Callable] = {}
        self._runs: dict[str, SubagentRun] = {}
        self._max_depth = max_depth
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def register_provider(self, name: str, fn: Callable) -> None:
        """Register a callable provider (mirror of registerProvider)."""
        if name in self._providers:
            raise ValueError(f"provider '{name}' already registered")
        self._providers[name] = fn

    def list_providers(self) -> list[str]:
        return list(self._providers.keys())

    def get_run(self, run_id: str) -> SubagentRun | None:
        return self._runs.get(run_id)

    def list_runs(self) -> list[dict]:
        return [
            {
                "id": r.id,
                "name": r.name,
                "status": r.status,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "stop_reason": r.stop_reason,
            }
            for r in self._runs.values()
        ]

    async def delegate_async(
        self,
        *,
        provider: str,
        parent_policy: DelegatedPolicy,
        specs: list[SubagentSpec],
    ) -> list[SubagentRun]:
        """Mirror of dsh SubagentRuntime.start with multiple specs.

        Each spec spawns a SubagentRun that executes the provider function in
        parallel. All runs inherit parent_policy (with approval forced to
        'never' per DSH delegation policy).
        """
        if provider not in self._providers:
            raise KeyError(f"unknown provider '{provider}'")

        # Mirror DSH's depth check
        if parent_policy.depth >= self._max_depth:
            raise RuntimeError(f"delegation depth {parent_policy.depth} exceeds max {self._max_depth}")

        # Capture delegated policy: child approval forced to 'never'
        child_policy = DelegatedPolicy(
            sandbox=parent_policy.sandbox,
            approval="never",  # DSH: delegated children always pin to 'never'
            tools=tuple(set(parent_policy.tools) | set(t for s in specs for t in s.tools)),
            depth=parent_policy.depth + 1,
            max_depth=self._max_depth,
        )

        async def run_one(spec: SubagentSpec) -> SubagentRun:
            run_id = f"run-{uuid.uuid4().hex[:12]}"
            run = SubagentRun(
                id=run_id,
                parent_id="orchestrator",
                name=spec.name,
                started_at=time.time(),
            )
            self._runs[run_id] = run
            try:
                # Filter tools per child policy (DSH: captureDelegatedPolicyOverrides)
                allowed_tools = child_policy.tools or None
                result = await self._invoke_provider(provider, spec, child_policy, allowed_tools)
                output = result.get("output", []) if isinstance(result, dict) else [str(result)]
                structured = result.get("structured") if isinstance(result, dict) else None
                stop = result.get("stop_reason", "completed")
                run.settle(output=output, structured=structured, stop_reason=stop)
            except Exception as e:
                run.status = "rejected"
                run.finished_at = time.time()
                run.diagnostic = f"{type(e).__name__}: {e}"
                run.stop_reason = "error"
            return run

        # Bounded parallelism via semaphore
        async def bounded(spec):
            async with self._semaphore:
                return await run_one(spec)

        runs = await asyncio.gather(*[bounded(s) for s in specs])
        return list(runs)

    def delegate_sync(self, *args, **kwargs) -> list[SubagentRun]:
        return asyncio.get_event_loop().run_until_complete(self.delegate_async(*args, **kwargs))

    async def _invoke_provider(
        self,
        provider_name: str,
        spec: SubagentSpec,
        policy: DelegatedPolicy,
        allowed_tools: tuple[str, ...] | None,
    ) -> Any:
        fn = self._providers[provider_name]
        if asyncio.iscoroutinefunction(fn):
            return await fn(spec, policy, allowed_tools)
        # Run sync fn in threadpool to avoid blocking
        return await asyncio.get_event_loop().run_in_executor(
            None, lambda: fn(spec, policy, allowed_tools)
        )


# Default providers (REAL GTMO integrations that benefit from parallelism)

def provider_prospect_city(spec: SubagentSpec, policy: DelegatedPolicy, allowed_tools) -> dict:
    """Default provider: prospect one city with one query, return leads.
    Uses the existing Google Maps Playwright integration.
    """
    from browser import google_maps
    query = spec.input.get("query", "business")
    city = spec.input.get("city", "")
    count = spec.input.get("count", 5)
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(google_maps.search(query, city, count))
    finally:
        loop.close()
    if not result.get("ok"):
        return {"output": [f"no results for {city}"], "stop_reason": "no_results"}
    leads = result.get("leads", [])
    return {
        "output": [f"{L.get('name','?')} | {L.get('phone','—')} | {L.get('website','—')}" for L in leads],
        "structured": leads,
        "stop_reason": "completed",
    }


def provider_prospex_score(spec: SubagentSpec, policy: DelegatedPolicy, allowed_tools) -> dict:
    """Default provider: score a list of leads with prospex."""
    from prospex_adapter import score_leads
    leads = spec.input.get("leads", [])
    target_industry = spec.input.get("target_industry")
    if not leads:
        return {"output": ["no leads"], "stop_reason": "empty"}
    scored = score_leads(leads, target_industry)
    return {
        "output": [f"{L['name']}: {L['score']} {L['priority']}" for L in scored],
        "structured": scored,
        "stop_reason": "completed",
    }


SOURCE = "REAL DSH subagent pattern (deepseek-harness/packages/subagent) — SubagentRuntime, delegation depth tracking, policy inheritance (approval='never'), one-shot fulfillment"


def default_runtime() -> SubagentRuntime:
    """Create the default SubagentRuntime with prospect + prospex providers."""
    rt = SubagentRuntime(max_depth=3, max_concurrent=8)
    rt.register_provider("prospect_city", provider_prospect_city)
    rt.register_provider("prospex_score", provider_prospex_score)
    return rt
