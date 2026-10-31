"""Tool catalog and per-pack grants. Developers write tools (CS Python, pack config, plugins); admins (no code) decide
which tools a teammate may use, cap them per day, and may tighten (never loosen) their tier.

Enforced like deny rules: the model only sees granted tools, a call to an ungranted tool is blocked and logged, and
the executor re-checks the grant, the daily cap and the tier when an action runs (so a revoke blocks pending actions).
"""
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Any
from zoneinfo import ZoneInfo

from app import config, events, registry
from app import db as store
from app.db import now
from app.policy import Decision

TIERS = ["allow", "confirm", "approve"]  # loosest to tightest
CORE = {"verify_identity", "confirm_action", "escalate", "reply_to_customer"}  # the loop needs these; never grantable
CS_TOOLS = {"get_order": ("read", "allow"), "get_tracking": ("read", "allow"), "send_notification": ("write", "allow"),
            "issue_refund": ("write", "confirm"), "create_reship": ("write", "confirm"), "cancel_order": ("write", "confirm")}
CACHE_TTL_S = 5.0


@dataclass(frozen=True)
class Manifest:
    name: str
    description: str
    effect: str
    loosest_tier: str
    source: str  # customer_service | config | engine | plugin
    secrets: tuple[str, ...] = ()
    label: str = ""


def pack_name() -> str:
    return registry.RUNTIME.pack.name if registry.RUNTIME else "customer_service"


def tighter(a: str, b: str) -> str:
    return a if TIERS.index(a) >= TIERS.index(b) else b


def catalog() -> dict[str, Manifest]:
    rt = registry.RUNTIME
    if rt is None:
        return {n: Manifest(n, registry.TOOL_MODELS[n][0], eff, tier, "customer_service") for n, (eff, tier) in CS_TOOLS.items()}
    out = {}
    for t in rt.pack.tools:
        if t.effect == "read":
            loosest = "allow"
        else:
            tiers = [x.tier for x in (t.rule.tiers if t.rule else [])] + ([t.rule.tier] if t.rule and t.rule.tier else [])
            loosest = min(tiers, key=TIERS.index) if tiers else "approve"
        out[t.name] = Manifest(t.name, t.description, t.effect, loosest, "config", label=t.label or "")
    out["send_notification"] = Manifest("send_notification", registry.TOOL_MODELS["send_notification"][0], "write", "allow", "engine",
                                        label="Notification")
    for name, p in rt.plugins.items():
        out[name] = Manifest(name, p.description, p.effect, p.loosest_tier, "plugin", p.secrets, p.label)
    return out


_cache: dict[str, Any] = {"at": 0.0, "grants": {}}


def invalidate(_event: Any = None) -> None:
    _cache["at"] = 0.0


events.on("grants", invalidate)  # in-process pub/sub drops the cache the moment an admin changes a grant


async def current() -> dict[str, dict[str, Any]]:
    if time.monotonic() - _cache["at"] > CACHE_TTL_S:
        _cache["grants"] = {g["tool"]: g async for g in store.db.tool_grants.find({"pack": pack_name()})}
        _cache["at"] = time.monotonic()
    return _cache["grants"]


async def ensure_defaults() -> None:
    """Startup. Customer service: every CS tool granted at its current tier, so its behaviour never changes.
    A pack with no grant rows yet: grant its config tools and send_notification; plugins stay ungranted."""
    await store.db.tool_grants.create_index([("pack", 1), ("tool", 1)], unique=True)
    pack = pack_name()
    if registry.RUNTIME is None:
        for name, m in catalog().items():
            await store.db.tool_grants.update_one({"pack": pack, "tool": name}, {"$set": {
                "enabled": True, "tier": m.loosest_tier, "max_per_day": None, "granted_by": "system", "granted_at": now()}}, upsert=True)
    elif not await store.db.tool_grants.count_documents({"pack": pack}):
        for name, m in catalog().items():
            if m.source != "plugin":
                await store.db.tool_grants.insert_one({"pack": pack, "tool": name, "enabled": True, "tier": m.loosest_tier,
                                                       "max_per_day": None, "granted_by": "system", "granted_at": now()})
    invalidate()


async def visible(specs: list[Any]) -> list[Any]:
    """The model's tool list this turn: core tools plus enabled grants."""
    g, cat = await current(), catalog()
    return [s for s in specs if s.name in CORE or (s.name in cat and g.get(s.name, {}).get("enabled"))]


async def _executed_today(tool: str) -> int:
    start = datetime.combine(datetime.now(ZoneInfo("Asia/Kolkata")).date(), dtime.min, ZoneInfo("Asia/Kolkata"))
    return await store.db.actions.count_documents({"tool": tool, "status": "executed", "updated_at": {"$gte": start}})


async def gate(tool: str) -> Decision | None:
    """Proposal time: refuse a tool that is not granted or has hit its daily cap. None = go ahead."""
    if tool not in catalog():
        return None
    g = (await current()).get(tool)
    if not g or not g.get("enabled"):
        return Decision("deny", "tool_not_granted", "This tool is not enabled for this assistant; offer to pass the request to the desk")
    if g.get("max_per_day") and await _executed_today(tool) >= g["max_per_day"]:
        return Decision("deny", "daily_cap", f"Daily limit of {g['max_per_day']} reached for this tool")
    return None


async def adjust(tool: str, decision: Decision) -> Decision:
    """After the rules decide: tighten to the granted tier, never loosen, never touch a deny or an escalation."""
    g = (await current()).get(tool)
    if not g or decision.result not in TIERS or tighter(g["tier"], decision.result) == decision.result:
        return decision
    return Decision(g["tier"], "grant_tier", f"An admin set this tool to need '{g['tier']}'")


async def recheck(action: dict[str, Any], approved: bool = False) -> Decision | None:
    """Run time: a revoked tool, an exhausted cap or a tier tightened after the proposal blocks the action."""
    tool = action["tool"]
    if tool not in catalog():
        return None
    invalidate()  # always read the latest grant when something is about to execute
    g = (await current()).get(tool)
    if not g or not g.get("enabled"):
        return Decision("deny", "tool_revoked", "This tool was revoked before the action ran")
    if g.get("max_per_day") and await _executed_today(tool) >= g["max_per_day"]:
        return Decision("deny", "daily_cap", f"Daily limit of {g['max_per_day']} reached for this tool")
    if not approved and action.get("tier") in TIERS and tighter(g["tier"], action["tier"]) != action["tier"]:
        return Decision("deny", "grant_tier_changed", f"An admin now requires '{g['tier']}' for this tool")
    return None


def validate_change(tool: str, tier: str | None, max_per_day: int | None) -> str | None:
    """Admin input check. Returns an error message, or None when valid."""
    m = catalog().get(tool)
    if m is None:
        return f"{tool} is not in this teammate's catalog"
    if tier is not None and (tier not in TIERS or TIERS.index(tier) < TIERS.index(m.loosest_tier)):
        return f"tier '{tier}' is looser than the manifest's loosest tier '{m.loosest_tier}'"
    if max_per_day is not None and max_per_day < 1:
        return "max_per_day must be at least 1"
    missing = [s for s in m.secrets if not getattr(config, s, None)]
    if missing:
        return f"missing secrets: {', '.join(missing)}"
    return None


async def change(tool: str, who: str, enabled: bool, tier: str | None = None, max_per_day: int | None = None,
                 clear_cap: bool = False) -> dict[str, Any]:
    pack = pack_name()
    before = await store.db.tool_grants.find_one({"pack": pack, "tool": tool}, {"_id": 0})
    m = catalog()[tool]
    after = {"pack": pack, "tool": tool, "enabled": enabled,
             "tier": tier or (before or {}).get("tier") or m.loosest_tier,
             "max_per_day": None if clear_cap else (max_per_day if max_per_day is not None else (before or {}).get("max_per_day")),
             "granted_by": who, "granted_at": now()}
    await store.db.tool_grants.replace_one({"pack": pack, "tool": tool}, after, upsert=True)
    await store.db.grant_audit.insert_one({"pack": pack, "tool": tool, "who": who, "at": now(), "before": before,
                                           "after": {k: after[k] for k in ("enabled", "tier", "max_per_day")}})
    events.publish("grants", tool=tool, enabled=enabled)
    return after
