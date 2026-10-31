"""In-process pub/sub fanned out over Server-Sent Events (single worker, no change streams)."""
import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from app.db import jsonable

_subscribers: set[tuple[asyncio.Queue, str | None]] = set()
_listeners: dict[str, list[Callable[[dict[str, Any]], None]]] = {}


def on(kind: str, fn: Callable[[dict[str, Any]], None]) -> None:
    """In-process listener (e.g. the grants cache) called synchronously on every event of this kind."""
    _listeners.setdefault(kind, []).append(fn)


def publish(kind: str, ticket_id: Any = None, session: str | None = None, **data: Any) -> None:
    """Fire-and-forget. `session` scopes an event to one customer chat; dashboards get everything."""
    event = jsonable({"kind": kind, "ticket_id": ticket_id, "session": session, **data})
    for fn in _listeners.get(kind, []):
        fn(event)
    for queue, scope in list(_subscribers):
        if scope is None or scope == session:
            queue.put_nowait(event)


async def stream(scope: str | None = None) -> AsyncIterator[str]:
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    entry = (queue, scope)
    _subscribers.add(entry)
    try:
        yield "retry: 2000\n\n"
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except TimeoutError:
                yield ": ping\n\n"
    finally:
        _subscribers.discard(entry)
