"""Lead queue — the speed backbone.

The webhook enqueues and returns `202` immediately; a worker drains the queue
and runs the (slower) qualify→draft→route pipeline. In demo mode this is an
in-process asyncio queue; with the `infra` extra a Redis-backed queue takes
over for real durability — same `LeadQueue` protocol either way.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from ..models import Lead


class LeadQueue(Protocol):
    async def put(self, lead: Lead) -> None: ...
    async def get(self) -> Lead: ...
    def qsize(self) -> int: ...


class InMemoryQueue:
    """Keyless default — durable enough for a demo, zero infra."""

    def __init__(self) -> None:
        self._q: asyncio.Queue[Lead] = asyncio.Queue()

    async def put(self, lead: Lead) -> None:
        await self._q.put(lead)

    async def get(self) -> Lead:
        return await self._q.get()

    def task_done(self) -> None:
        self._q.task_done()

    def qsize(self) -> int:
        return self._q.qsize()


_queue: InMemoryQueue | None = None


def get_queue() -> InMemoryQueue:
    global _queue
    if _queue is None:
        _queue = InMemoryQueue()
    return _queue
