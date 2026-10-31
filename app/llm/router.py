"""LLM router: first healthy lane wins; failures cool a lane down; per-lane counters feed the dashboard footer."""
import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app import config
from app.llm.base import Adapter, LaneFailure
from app.llm.gemini import GeminiAdapter
from app.llm.keys import KeyRing
from app.llm.openai_compat import OpenAICompatAdapter

T = TypeVar("T")
KEYS = {"gemini": KeyRing(config.GEMINI_API_KEY), "groq": KeyRing(config.GROQ_API_KEY), "openrouter": KeyRing(config.OPENROUTER_API_KEY)}
BASE_COOLDOWN_S = 30.0
MAX_COOLDOWN_S = 300.0


def _next_pacific_midnight() -> float:
    pt = datetime.now(ZoneInfo("America/Los_Angeles"))
    midnight = (pt + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
    return midnight.timestamp()


class Lane:
    def __init__(self, spec: str) -> None:
        self.name = spec
        self.provider, self.model = spec.split(":", 1)
        self.adapter: Adapter = (GeminiAdapter(self.model, KEYS["gemini"]) if self.provider == "gemini"
                                 else OpenAICompatAdapter(self.provider, self.model, KEYS[self.provider]))
        self.calls = self.ok = self.fails = self.consecutive = 0
        self.cooldown_until = 0.0
        self.last_error = ""
        self.fault_count = 0  # test hook: fail this many calls with a fake 429 ...
        self.fault_after = 0  # ... after letting this many calls through

    def healthy(self) -> bool:
        return time.time() >= self.cooldown_until

    def record_failure(self, f: LaneFailure) -> None:
        self.fails += 1
        self.consecutive += 1
        self.last_error = str(f)[:160]
        if f.daily:
            until = _next_pacific_midnight() if self.provider == "gemini" else time.time() + 86400
        else:
            backoff = min(BASE_COOLDOWN_S * 2 ** (self.consecutive - 1), MAX_COOLDOWN_S)
            until = time.time() + (f.retry_after or backoff)
        self.cooldown_until = max(self.cooldown_until, until)

    def status(self) -> dict[str, Any]:
        return {"lane": self.name, "calls": self.calls, "ok": self.ok, "fails": self.fails,
                "cooling_s": max(0, round(self.cooldown_until - time.time())), "last_error": self.last_error}


class Router:
    def __init__(self) -> None:
        self.agent = [Lane(s) for s in config.LLM_LANES]
        by_provider = {lane.provider: lane for lane in self.agent if lane.provider != "gemini"}
        self.light = [Lane(f"gemini:{config.LLM_LIGHT_MODEL}"), *[by_provider[p] for p in ("groq", "openrouter") if p in by_provider]]

    def lanes(self, light: bool = False) -> list[Lane]:
        return [lane for lane in (self.light if light else self.agent) if lane.healthy()]

    def find(self, name: str) -> Lane | None:
        return next((lane for lane in [*self.agent, *self.light] if lane.name == name), None)

    async def call(self, lane: Lane, fn: Callable[[], Awaitable[T]], timeout: float = config.LLM_TIMEOUT_S) -> T:
        """One model call on a lane, with timeout, fault injection and counters. Raises LaneFailure."""
        lane.calls += 1
        if lane.fault_count > 0:
            if lane.fault_after > 0:
                lane.fault_after -= 1
            else:
                lane.fault_count -= 1
                raise LaneFailure("forced", status=429, detail="injected fault")
        try:
            result = await asyncio.wait_for(fn(), timeout)
        except TimeoutError as e:
            raise LaneFailure("timeout", detail=f"> {timeout}s") from e
        lane.ok += 1
        lane.consecutive = 0
        return result

    async def structured(self, prompt: str, model: type[BaseModel]) -> BaseModel:
        """Light, no-tool call (summaries) with JSON-schema output, failing over across light lanes."""
        errors = []
        for lane in self.lanes(light=True):
            try:
                return await self.call(lane, lambda: lane.adapter.structured(prompt, model), timeout=20)
            except LaneFailure as f:
                lane.record_failure(f)
                errors.append(f"{lane.name}: {f}")
        raise LaneFailure("all_lanes_failed", detail="; ".join(errors))

    def status(self) -> list[dict[str, Any]]:
        seen, out = set(), []
        for lane in [*self.agent, *self.light]:
            if lane.name not in seen:
                seen.add(lane.name)
                out.append(lane.status())
        return out


router = Router()
