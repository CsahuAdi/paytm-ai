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
    return await reset(demo_contacts=not fake)


class Fault(BaseModel):
    lane: str
    count: int = 1
    after: int = 0


@router.post("/debug/fault")
async def debug_fault(f: Fault) -> dict:
    _require_debug()
    lane = llm_router.find(f.lane)
    if lane is None:
        raise HTTPException(404, f"no lane {f.lane}")
    lane.fault_count, lane.fault_after = f.count, f.after
    return {"lane": lane.name, "fault_count": f.count, "fault_after": f.after}


@router.post("/debug/heal")
async def debug_heal() -> dict:
    _require_debug()
    for lane in [*llm_router.agent, *llm_router.light]:
        lane.cooldown_until = 0
        lane.consecutive = 0
        lane.fault_count = lane.fault_after = 0
    return {"lanes": llm_router.status()}
