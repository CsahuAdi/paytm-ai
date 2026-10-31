"""Simulated "Paytm insights" feed: a deterministic rule over (fake) transaction data proposes win-back coupons that the
merchant approves one by one. No LLM, no Paytm API. Only mounted for packs that have the issue_coupon plugin."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import config
from app import db as store
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


@router.get("/stats")
async def stats() -> dict:
    """Dashboard strip: coupons issued and redeemed, budget left, and revenue brought back. All SIMULATED data.
    The coupon seeded for the 14-day-cap story carries seed=true and is not counted."""
    merchant = await store.db.merchants.find_one({}) or {}
    base = {"seed": {"$ne": True}}
    revenue = 0
    async for t in store.db.transactions.find({"source": "paytm_pay", "coupon_code": {"$ne": None}}):
        revenue += t.get("gross", t["amount"])
    return {"issued": await store.db.coupons.count_documents(base), "redeemed": await store.db.coupons.count_documents(base | {"status": "redeemed"}),
            "budget_remaining": merchant.get("budget_remaining"), "monthly_budget": merchant.get("monthly_budget"),
            "revenue_brought_back": revenue, "simulated": True}
