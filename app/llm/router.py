"""LLM router: first healthy lane wins; failures cool a lane down; per-lane counters feed the dashboard footer."""
import asyncio
import json
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
USAGE_PATH = f"{config.ROOT}/data/lane_usage.json"
BASE_COOLDOWN_S = 30.0
MAX_COOLDOWN_S = 300.0


def _pacific_day() -> str:
    return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


def _load_usage() -> dict[str, Any]:
    try:
        with open(USAGE_PATH) as f:
            data = json.load(f)
        return data if data.get("day") == _pacific_day() else {"day": _pacific_day(), "calls": {}}
    except (OSError, ValueError):
        return {"day": _pacific_day(), "calls": {}}


USAGE = _load_usage()


def _count_call(lane: str) -> int:
    global USAGE
    USAGE = _load_usage()  # re-read: several server instances share this file and the same free-tier quota
    USAGE["calls"][lane] = USAGE["calls"].get(lane, 0) + 1
    with open(USAGE_PATH, "w") as f:
        json.dump(USAGE, f)
    return USAGE["calls"][lane]


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
        self.daily_off_until = 0.0
        self.last_error = ""
        limit = config.DAILY_LIMITS.get(spec)
        if limit and USAGE["calls"].get(spec, 0) >= limit:  # known exhausted today (survives restarts)
            self.daily_off_until = _next_pacific_midnight()
        self.fault_count = 0  # test hook: fail this many calls with a fake 429 ...
        self.fault_after = 0  # ... after letting this many calls through

    def healthy(self) -> bool:
        return time.time() >= max(self.cooldown_until, self.daily_off_until)

    def record_failure(self, f: LaneFailure) -> None:
        self.fails += 1
        self.consecutive += 1
        self.last_error = str(f)[:160]
        if f.daily:
            self.daily_off_until = _next_pacific_midnight() if self.provider == "gemini" else time.time() + 86400
            return
        backoff = min(BASE_COOLDOWN_S * 2 ** (self.consecutive - 1), MAX_COOLDOWN_S)
        self.cooldown_until = max(self.cooldown_until, time.time() + (f.retry_after or backoff))

    def status(self) -> dict[str, Any]:
        return {"lane": self.name, "calls": self.calls, "ok": self.ok, "fails": self.fails,
                "cooling_s": max(0, round(self.cooldown_until - time.time())),
                "daily_off": self.daily_off_until > time.time(), "today": USAGE["calls"].get(self.name, 0),
                "daily_limit": config.DAILY_LIMITS.get(self.name),
                "last_error": self.last_error}


class Router:
    def __init__(self) -> None:
        self.agent = [Lane(s) for s in config.LLM_LANES]
        by_provider = {lane.provider: lane for lane in self.agent if lane.provider != "gemini"}
        self.light = [Lane(f"gemini:{config.LLM_LIGHT_MODEL}"), *[by_provider[p] for p in ("groq", "openrouter") if p in by_provider]]

    def lanes(self, light: bool = False) -> list[Lane]:
        pool = self.light if light else self.agent
        healthy = [lane for lane in pool if lane.healthy()]
        if healthy:
            return healthy
        # Every lane is cooling down: try them anyway, soonest cooldown first (never the ones off for the day), so one
        # transient 5xx does not black the agent out for a whole cooldown window.
        return sorted((lane for lane in pool if lane.daily_off_until <= time.time()), key=lambda lane: lane.cooldown_until)

    def find(self, name: str) -> Lane | None:
        return next((lane for lane in [*self.agent, *self.light] if lane.name == name), None)

    async def call(self, lane: Lane, fn: Callable[[], Awaitable[T]], timeout: float = config.LLM_TIMEOUT_S) -> T:
        """One model call on a lane, with timeout, fault injection and counters. Raises LaneFailure."""
        lane.calls += 1
        if lane.fault_count > 0 and lane.fault_after <= 0:
            lane.fault_count -= 1
            raise LaneFailure("forced", status=429, detail="injected fault")
        if lane.fault_count > 0:
            lane.fault_after -= 1
        _count_call(lane.name)
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
        global USAGE
        USAGE = _load_usage()
        seen, out = set(), []
        for lane in [*self.agent, *self.light]:
            if lane.name not in seen:
                seen.add(lane.name)
                out.append(lane.status())
        return out


router = Router()
