"""Simulated "Paytm insights" feed: a deterministic rule over (fake) transaction data proposes win-back coupons that the
merchant approves one by one. No LLM, no Paytm API. Only mounted for packs that have the issue_coupon plugin."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import config
from app.db import jsonable

router = APIRouter(prefix="/api/insights")


class RunIn(BaseModel):
    name: str


@router.post("/run")
async def run(body: RunIn) -> dict:
    if body.name not in config.HUMAN_AGENTS:  # demo-level roles: a label, not authentication
        raise HTTPException(403, f"{body.name!r} is not a reviewer, merchant or admin")
    from plugins.kirana import paytm_insights
    return jsonable(await paytm_insights(body.name))
