"""Meters (always on) and test hooks (DEBUG=1 only): reset seed data, inject LLM lane faults."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import config
from app.llm.router import router as llm_router
from app.seed_data import reset

router = APIRouter()


@router.get("/api/meters")
async def meters() -> dict:
    from app.lang import sarvam_meter
    return {"lanes": llm_router.status(), "sarvam": sarvam_meter.status()}


def _require_debug() -> None:
    if not config.DEBUG:
        raise HTTPException(404)


@router.post("/debug/reset")
async def debug_reset(fake: bool = False) -> dict:
    """fake=1 keeps the fixture contacts (tests); the default maps the demo customer to DEMO_CUSTOMER_* from .env."""
    _require_debug()
    for lane in [*llm_router.agent, *llm_router.light]:
        lane.fault_count = lane.fault_after = 0
    counts = await reset(demo_contacts=not fake)
    from app import grants
    grants.invalidate()
    return counts


class Fault(BaseModel):
    lane: str
    count: int = 1
    after: int = 0


@router.post("/debug/fault")
async def debug_fault(f: Fault) -> dict:
    _require_debug()
    lanes = [*llm_router.agent, *llm_router.light] if f.lane == "*" else [llm_router.find(f.lane)]
    if None in lanes:
        raise HTTPException(404, f"no lane {f.lane}")
    for lane in lanes:
        lane.fault_count, lane.fault_after = f.count, f.after
    return {"lanes": [lane.name for lane in lanes], "fault_count": f.count, "fault_after": f.after}


@router.post("/debug/heal")
async def debug_heal() -> dict:
    _require_debug()
    for lane in [*llm_router.agent, *llm_router.light]:
        lane.cooldown_until = 0
        lane.consecutive = 0
        lane.fault_count = lane.fault_after = 0
    return {"lanes": llm_router.status()}


# ---------- pack test hooks (DEBUG=1 only): run the deterministic rules without a model call ----------

class DebugTool(BaseModel):
    customer_id: str  # the pack's subject id, e.g. C-101
    tool: str
    args: dict = {}
    channel: str = "paytm"


@router.post("/debug/tool")
async def debug_tool(c: DebugTool) -> dict:
    """Propose one tool call as a verified customer, through the same grant gate, validation, rules and executor as the model."""
    _require_debug()
    from pydantic import ValidationError

    from app import db as store
    from app import grants, registry, tickets
    from app.db import jsonable, now
    from app.tools import Ctx, record
    rt = registry.RUNTIME
    if rt is None:
        raise HTTPException(400, "pack instances only")
    customer = await store.db.customers.find_one({rt.pack.verification.lookup["field"]: c.customer_id})
    spec = registry.TOOL_MODELS.get(c.tool)
    if customer is None or spec is None:
        raise HTTPException(404, "unknown customer or tool")
    try:
        args = spec[1].model_validate(c.args)  # the model-facing validation: extra keys such as an amount are refused
    except ValidationError as e:
        raise HTTPException(422, str(e)[:300]) from e
    identity = await tickets.auto_verify(await tickets.get_identity(c.channel, customer["phone"]), customer, "debug")
    ticket = await tickets.open_ticket(identity, c.channel, customer.get("language", "en-IN"))
    ctx = Ctx(ticket=ticket, identity=identity, customer=customer, channel=c.channel, message_at=now())
    gated = await grants.gate(c.tool)
    if gated:
        await record(ctx, c.tool, args.model_dump(), gated, "blocked")
        return {"blocked": True, "rule_id": gated.rule_id, "reason": gated.reason}
    return jsonable(await registry.execute(ctx, c.tool, args))


@router.post("/debug/confirm/{action_id}")
async def debug_confirm(action_id: str) -> dict:
    """Simulate the customer's 'yes' on a pending_confirmation action (compare-and-set, then the executor)."""
    _require_debug()
    from bson import ObjectId
    from pymongo import ReturnDocument

    from app import db as store
    from app.db import jsonable, now
    from app.tools import run_action
    claimed = await store.db.actions.find_one_and_update(
        {"_id": ObjectId(action_id), "status": "pending_confirmation"},
        {"$set": {"status": "executing", "decided_by": "customer", "decided_at": now()}}, return_document=ReturnDocument.AFTER)
    if claimed is None:
        raise HTTPException(409, "action is not pending_confirmation")
    return jsonable(await run_action(claimed))


class DebugSet(BaseModel):
    collection: str
    filter: dict = {}
    set: dict


@router.post("/debug/set")
async def debug_set(c: DebugSet) -> dict:
    """Edit seed data mid-test (e.g. empty the coupon budget). Accepts '@days_ago:N' dates."""
    _require_debug()
    from app import db as store
    from app.packs.runtime import resolve_dates
    res = await store.db[c.collection].update_many(c.filter, {"$set": resolve_dates(c.set)})
    return {"matched": res.matched_count}
