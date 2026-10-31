"""Kirana pack plugins: the coupon logic pack config cannot express (computed amounts, a shared budget, a 14-day
window, generated codes) and the simulated "Paytm insights" rule. All data is fake; nothing calls Paytm.

- get_resolution_options (read): refund and coupon side by side for one transaction, recorded as presented.
- issue_coupon (write, loosest tier confirm): the amount is always computed here, never supplied by the model.
  Complaint source: min(max_coupon, floor(amount x (1 + bonus_pct/100))), confirm tier (the customer's choice).
  Insight source (only the feed creates these): insight_coupon, approve tier (the merchant), opt-in required.
"""
import math
import random
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from app import db as store
from app.db import jsonable, now
from app.policy import Decision
from plugins import plugin_tool

HARD_NO_COUPON = {"failed", "duplicate", "refunded"}  # these go to a refund/reversal or escalation, never a coupon


class TxnParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: str = Field(pattern=r"^T-\d{4}$", description="Transaction ID, e.g. T-9001")


async def merchant() -> dict[str, Any]:
    return await store.db.merchants.find_one({}) or {}


async def _own_txn(txn_id: str | None, member: dict[str, Any] | None) -> dict[str, Any] | None:
    if not txn_id or not member:
        return None
    return await store.db.transactions.find_one({"txn_id": txn_id, "customer_id": member.get("customer_id")})


async def _recent_coupon(member: dict[str, Any], days: int) -> dict[str, Any] | None:
    return await store.db.coupons.find_one({"customer_id": member.get("customer_id"), "created_at": {"$gte": now() - timedelta(days=days)}})


def complaint_amount(txn_amount: float, m: dict[str, Any]) -> int:
    return int(min(m.get("max_coupon", 0), math.floor(txn_amount * (1 + m.get("bonus_pct", 0) / 100))))


async def coupon_decision(params: dict[str, Any], member: dict[str, Any] | None) -> tuple[Decision, int]:
    """Every coupon rule, re-checked at execution. Returns (decision, computed amount)."""
    m = await merchant()
    source = params.get("source", "complaint")
    if member is None:
        return Decision("deny", "unverified", "Customer not verified"), 0
    if source == "insight":
        if not member.get("marketing_opt_in"):
            return Decision("deny", "no_opt_in", "Customer has not opted in to offers"), 0
        amount, tier, rule = int(m.get("insight_coupon", 0)), "approve", "insight_coupon"
    else:
        txn = await _own_txn(params.get("transaction_id"), member)
        if txn is None:
            return Decision("deny", "not_found", "No such transaction for this customer"), 0
        if txn["status"] in HARD_NO_COUPON:
            return Decision("deny", "no_coupon_for_reversal", f"A {txn['status']} payment gets a reversal or escalation, never a coupon"), 0
        if txn.get("resolution", "none") != "none":
            return Decision("deny", "already_resolved", "This payment already has a resolution"), 0
        if not (txn.get("options_presented") or {}).get("coupon"):
            return Decision("deny", "options_not_presented", "Refund and coupon must be shown together first"), 0
        amount, tier, rule = complaint_amount(txn["amount"], m), "confirm", "coupon_choice"
    window = int(m.get("coupon_window_days", 14))
    if await _recent_coupon(member, window):
        return Decision("deny", "coupon_window", f"A coupon was already issued to this customer in the last {window} days"), amount
    if m.get("budget_remaining", 0) < amount:
        return Decision("deny", "budget_exhausted", f"Coupon budget left Rs {m.get('budget_remaining', 0)} is below Rs {amount}"), amount
    return Decision(tier, rule, f"Coupon Rs {amount} ({source})"), amount


@plugin_tool(name="get_resolution_options", label="Resolution options", params=TxnParams, effect="read", loosest_tier="allow",
             description="For one transaction, compute the options to show the customer side by side: refund to source "
                         "(amount, method, timeline) and, when allowed, a coupon (amount set by store policy). Call before "
                         "refund_to_source or issue_coupon.",
             idempotency=lambda p: f"options:{p['transaction_id']}")
class ResolutionOptions:
    async def check(self, params: dict[str, Any], member: dict[str, Any] | None) -> Decision:
        if await _own_txn(params.get("transaction_id"), member) is None:
            return Decision("deny", "not_found", "No such transaction for this customer")
        return Decision("allow", "read", "Allowed")

    async def run(self, action: dict[str, Any], member: dict[str, Any]) -> dict[str, Any]:
        from app import grants
        m = await merchant()
        txn = await _own_txn(action["args"]["transaction_id"], member)
        refund: dict[str, Any] | None = None
        refund_reason = None
        if txn["status"] == "success" and txn.get("resolution", "none") == "none":
            refund = {"amount": txn["amount"], "method": "original Paytm payment method", "eta": m.get("refund_eta", "3-5 working days")}
        elif txn["status"] == "failed":
            refund_reason = "A failed payment is reversed automatically by the bank; nothing to refund here"
        elif txn["status"] == "duplicate":
            refund_reason = "Duplicate charge: escalate (fraud_signal) so the payments team reverses it"
        else:
            refund_reason = f"Already resolved ({txn.get('resolution')})"
        coupon: dict[str, Any] | None = None
        coupon_reason = None
        granted = (await grants.current()).get("issue_coupon", {}).get("enabled")
        if not granted:
            coupon_reason = "Coupons are not available right now"
        else:
            decision, amount = await coupon_decision({"transaction_id": txn["txn_id"]} | {"_presenting": True}, member)
            if decision.result == "deny" and decision.rule_id != "options_not_presented":
                coupon_reason = decision.reason
            else:
                coupon = {"amount": amount, "valid_days": int(m.get("coupon_valid_days", 14)),
                          "where": f"next Paytm payment at {m.get('name', 'this store')}"}
        await store.db.transactions.update_one({"_id": txn["_id"]}, {"$set": {"options_presented": {
            "refund": refund is not None, "coupon": coupon["amount"] if coupon else None, "ticket": action.get("ticket_id"), "at": now()}}})
        return jsonable({"transaction_id": txn["txn_id"], "status": txn["status"], "refund": refund, "refund_unavailable": refund_reason,
                         "coupon": coupon, "coupon_unavailable": coupon_reason,
                         "present": "Show the available options side by side with equal weight; the customer chooses."})


@plugin_tool(name="issue_coupon", label="Coupon", params=TxnParams, effect="write", loosest_tier="confirm",
             description="Issue the store's coupon for a transaction the customer complained about, after they chose it over "
                         "the refund. The amount is computed by store policy; never state or ask for an amount. The customer "
                         "must say yes first.",
             idempotency=lambda p: f"coupon:{p.get('transaction_id') or p.get('customer_id')}")
class IssueCoupon:
    async def check(self, params: dict[str, Any], member: dict[str, Any] | None) -> Decision:
        decision, _ = await coupon_decision(params, member)
        return decision

    async def run(self, action: dict[str, Any], member: dict[str, Any]) -> dict[str, Any]:
        await store.db.coupons.create_index("code", unique=True)
        p = action["args"]
        source = p.get("source", "complaint")
        decision, amount = await coupon_decision(p, member)
        if decision.result == "deny":
            return {"error": decision.reason, "rule_id": decision.rule_id}
        m = await merchant()
        spent = await store.db.merchants.update_one({"_id": m["_id"], "budget_remaining": {"$gte": amount}},
                                                    {"$inc": {"budget_remaining": -amount}})
        if not spent.modified_count:
            return {"error": "Coupon budget exhausted", "rule_id": "budget_exhausted"}
        expires = now() + timedelta(days=int(m.get("coupon_valid_days", 14)))
        for _ in range(20):
            code = "KIRANA-" + "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(4))
            try:
                await store.db.coupons.insert_one({"code": code, "customer_id": member["customer_id"], "merchant_id": m["merchant_id"],
                                                   "amount": amount, "status": "issued", "source": source, "txn_id": p.get("transaction_id"),
                                                   "created_at": now(), "expires_at": expires, "redeemed_at": None})
                break
            except DuplicateKeyError:
                continue
        if source == "complaint":
            await store.db.transactions.update_one({"txn_id": p["transaction_id"]}, {"$set": {"resolution": "coupon"}})
        name = member.get("name_latin") or member.get("name")
        message = (f"Hi {name}, we miss you at {m.get('name')}! Here is a Rs {amount} coupon, {code}, for your next Paytm "
                   f"payment at the store. Valid until {expires.date().isoformat()}.") if source == "insight" else None
        return jsonable({"code": code, "amount": amount, "expires_at": expires.date().isoformat(), "source": source,
                         "budget_remaining": m.get("budget_remaining", 0) - amount, "customer_message": message})


async def paytm_insights(who: str) -> dict[str, Any]:
    """Simulated "Paytm insights": regulars (3+ successful payments in 60 days) who went quiet (none in 14 days), opted in,
    with no coupon in 14 days. Each match becomes a pending_approval insight coupon for the merchant. Deterministic, no LLM."""
    from app import grants, tickets
    from app.tools import Ctx, record
    gated = await grants.gate("issue_coupon")
    if gated:
        return {"error": "issue_coupon is not granted: ask an admin to grant it in the Tools tab", "suggested": [], "skipped": []}
    m = await merchant()
    today = now()
    year, week, _ = today.isocalendar()
    suggested, skipped = [], []
    async for cust in store.db.customers.find({}):
        recent = [t async for t in store.db.transactions.find({"customer_id": cust["customer_id"], "status": "success",
                                                                "date": {"$gte": today - timedelta(days=60)}})]
        if len(recent) < 3:
            continue
        last = max(t["date"] for t in recent)
        days_since = (today - last).days
        if days_since < 14:
            continue  # still a regular visitor: not a win-back case
        row = {"customer": cust.get("name_latin") or cust["name"], "customer_id": cust["customer_id"], "visits_60d": len(recent),
               "days_since_last": days_since}
        decision, amount = await coupon_decision({"source": "insight"}, cust)
        if decision.result == "deny":
            skipped.append(row | {"reason": decision.reason})
            continue
        identity = await tickets.auto_verify(await tickets.get_identity("paytm", cust["phone"]), cust, "paytm_sender")
        ticket = await tickets.open_ticket(identity, "paytm", cust.get("language", "en-IN"))
        await store.db.tickets.update_one({"_id": ticket["_id"]}, {"$set": {"contact": {"channel": "paytm", "sender": cust["phone"]}}})
        ctx = Ctx(ticket=ticket, identity=identity, customer=cust, channel="paytm", message_at=today)
        args = {"source": "insight", "customer_id": cust["customer_id"], "amount": amount, "visits_60d": len(recent),
                "days_since_last": days_since, "budget_remaining": m.get("budget_remaining"), "proactive": True}
        try:
            await record(ctx, "issue_coupon", args, decision, "pending_approval", key=f"kirana:insight:{cust['customer_id']}:{year}-W{week}")
        except DuplicateKeyError:
            skipped.append(row | {"reason": "already proposed this week"})
            continue
        await tickets.set_status(ticket["_id"], "pending_approval")
        suggested.append(row | {"amount": amount})
    return {"suggested": suggested, "skipped": skipped, "budget_remaining": m.get("budget_remaining"), "run_by": who,
            "note": "Simulated Paytm insights on fake data"}
