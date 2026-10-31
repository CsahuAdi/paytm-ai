"""Dashboard API: tickets, timeline, Needs-attention queue, approvals (compare-and-set) and takeover."""
import statistics
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pymongo import ReturnDocument

from app import config, core, events, registry, tickets
from app import db as store
from app.db import jsonable, now
from app.tools import run_action

router = APIRouter(prefix="/api")


class HumanIn(BaseModel):
    name: str
    note: str = ""
    text: str = ""


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except InvalidId as e:
        raise HTTPException(404, "not found") from e


def _human(name: str) -> str:
    if name not in config.HUMAN_AGENTS:
        raise HTTPException(400, f"pick a name from {config.HUMAN_AGENTS}")
    return name


async def _customers(ids: list[Any]) -> dict[Any, dict[str, Any]]:
    return {c["_id"]: c async for c in store.db.customers.find({"_id": {"$in": [i for i in ids if i]}})}


@router.get("/config")
async def ui_config() -> dict:
    rt = registry.RUNTIME
    labels = {"subject": "customer", "primary": "orders", "money": "refunds", "title": "Support desk"}
    if rt is None:
        return {"product": config.PRODUCT_NAME, "humans": config.HUMAN_AGENTS, "languages": config.LANGUAGES,
                "pack": None, "labels": labels, "tool_labels": {}}
    return {"product": rt.pack.product, "humans": config.HUMAN_AGENTS, "languages": config.LANGUAGES, "pack": rt.pack.name,
            "labels": labels | {"title": "Desk"} | rt.pack.labels, "tool_labels": rt.tool_labels()}


@router.get("/tickets")
async def list_tickets() -> list[dict]:
    rows = [t async for t in store.db.tickets.find({"status": {"$ne": "merged"}}).sort("updated_at", -1).limit(60)]
    custs = await _customers([t.get("customer_id") for t in rows])
    out = []
    for t in rows:
        last = await store.db.messages.find_one({"ticket_id": t["_id"]}, sort=[("created_at", -1)])
        c = custs.get(t.get("customer_id"))
        pending = await store.db.actions.count_documents({"ticket_id": t["_id"], "status": {"$in": ["pending_approval", "pending_confirmation"]}})
        out.append({"id": str(t["_id"]), "status": t["status"], "mode": t["mode"], "channels": t.get("channels", []),
                    "language": t.get("language"), "customer": c["name"] if c else "Unverified visitor", "tier": c["tier"] if c else None,
                    "escalation": (t.get("escalation") or {}).get("rule_id"), "pending": pending,
                    "last": (last or {}).get("text_en", "")[:90], "last_channel": (last or {}).get("channel"),
                    "updated_at": jsonable(t["updated_at"])})
    return out


@router.get("/tickets/{ticket_id}")
async def ticket_detail(ticket_id: str) -> dict:
    t = await store.db.tickets.find_one({"_id": _oid(ticket_id)})
    if t is None:
        raise HTTPException(404, "ticket not found")
    c = await store.db.customers.find_one({"_id": t.get("customer_id")}) if t.get("customer_id") else None
    msgs = [m async for m in store.db.messages.find({"ticket_id": t["_id"]})]
    acts = [a async for a in store.db.actions.find({"ticket_id": t["_id"]})]
    timeline = sorted([{"type": "message", **jsonable(m)} for m in msgs] + [{"type": "action", **jsonable(a)} for a in acts],
                      key=lambda x: x["created_at"])
    orders = [o async for o in store.db.orders.find({"customer_id": c["_id"]})] if c and not registry.RUNTIME else []
    return {"ticket": jsonable(t), "customer": jsonable({k: v for k, v in (c or {}).items() if k != "phone"}) if c else None,
            "orders": [{"order_number": o["order_number"], "status": o["status"], "total": o["total"],
                        "refund_total": o.get("refund_total", 0)} for o in orders],
            "records": await registry.RUNTIME.records(c) if registry.RUNTIME else [],
            "timeline": timeline}


async def _card(a: dict[str, Any], custs: dict[Any, dict[str, Any]]) -> dict[str, Any]:
    t = await store.db.tickets.find_one({"_id": a["ticket_id"]})
    c = custs.get(a.get("customer_id"))
    last3 = [m async for m in store.db.messages.find({"ticket_id": a["ticket_id"]}).sort("created_at", -1).limit(3)]
    record = await registry.RUNTIME.target(a) if registry.RUNTIME else None
    return {"action_id": str(a["_id"]), "ticket_id": str(a["ticket_id"]), "tool": a["tool"], "args": a["args"], "record": record,
            "rule_id": a["decision"]["rule_id"], "reason": a["decision"]["reason"], "customer": c["name"] if c else None,
            "tier": c["tier"] if c else None, "summary": (t.get("escalation") or {}).get("handoff_summary") or t.get("summary") or None,
            "last_messages": [{"author": m["author"], "text_en": m["text_en"]} for m in reversed(last3)],
            "created_at": jsonable(a["created_at"])}


@router.get("/queue")
async def queue() -> dict:
    """Needs attention: held actions (VIP first, then oldest) and escalated tickets nobody has taken."""
    held = [a async for a in store.db.actions.find({"status": "pending_approval"}).sort("created_at", 1)]
    custs = await _customers([a.get("customer_id") for a in held])
    held.sort(key=lambda a: (custs.get(a.get("customer_id"), {}).get("tier") != "vip", a["created_at"]))
    esc = [t async for t in store.db.tickets.find({"status": "escalated", "mode": "ai"}).sort("updated_at", 1)]
    ecusts = await _customers([t.get("customer_id") for t in esc])
    esc.sort(key=lambda t: ecusts.get(t.get("customer_id"), {}).get("tier") != "vip")
    return {"approvals": [await _card(a, custs) for a in held],
            "escalations": [{"ticket_id": str(t["_id"]), "customer": ecusts.get(t.get("customer_id"), {}).get("name", "Unverified visitor"),
                             "tier": ecusts.get(t.get("customer_id"), {}).get("tier"), "rule_id": (t.get("escalation") or {}).get("rule_id"),
                             "reason": (t.get("escalation") or {}).get("reason"),
                             "handoff_summary": (t.get("escalation") or {}).get("handoff_summary")} for t in esc]}


def _outcome_text(action: dict[str, Any], result: dict[str, Any]) -> str:
    args = action["args"]
    if result.get("blocked"):
        return f"A teammate reviewed your request, but it can't be completed: {result.get('reason', 'it is not allowed by our policy')}."
    if registry.RUNTIME and action["tool"] in registry.RUNTIME.tools:
        label = registry.RUNTIME.tool_labels()[action["tool"]].lower()
        return f"Good news: a staff member approved your {label} request. It has been processed."
    if action["tool"] == "issue_refund":
        return f"Good news: a teammate approved your refund of Rs {args['amount']:,} for order {args['order_number']}. It has been processed."
    return f"A teammate approved your request for order {args.get('order_number', '')}. It has been processed."


@router.post("/actions/{action_id}/approve")
async def approve(action_id: str, body: HumanIn) -> dict:
    name = _human(body.name)
    claimed = await store.db.actions.find_one_and_update(
        {"_id": _oid(action_id), "status": "pending_approval"},
        {"$set": {"status": "executing", "decided_by": name, "decided_at": now(), "decision_note": body.note or None}},
        return_document=ReturnDocument.AFTER)
    if claimed is None:
        raise HTTPException(409, "action is not pending approval (already decided, blocked or missing)")
    async with tickets.lock_for(claimed["ticket_id"]):
        result = await run_action(claimed, approved=True)  # re-checks every deny rule against current data
        await core.deliver(claimed["ticket_id"], _outcome_text(claimed, result), author="ai")
        await core.settle_status(claimed["ticket_id"])
    events.publish("approval", claimed["ticket_id"], action_id=action_id, decision="approved", by=name)
    return {"status": "blocked" if result.get("blocked") else "executed", "result": jsonable(result)}


@router.post("/actions/{action_id}/reject")
async def reject(action_id: str, body: HumanIn) -> dict:
    name = _human(body.name)
    claimed = await store.db.actions.find_one_and_update(
        {"_id": _oid(action_id), "status": "pending_approval"},
        {"$set": {"status": "rejected", "decided_by": name, "decided_at": now(), "decision_note": body.note or None}},
        return_document=ReturnDocument.AFTER)
    if claimed is None:
        raise HTTPException(409, "action is not pending approval")
    note = f" Note from {name}: {body.note}" if body.note else ""
    async with tickets.lock_for(claimed["ticket_id"]):
        await core.deliver(claimed["ticket_id"], f"A teammate reviewed your request and couldn't approve it.{note}", author="ai")
        await core.settle_status(claimed["ticket_id"])
    events.publish("approval", claimed["ticket_id"], action_id=action_id, decision="rejected", by=name)
    return {"status": "rejected"}


async def _timeline_event(ticket_id: ObjectId, tool: str, name: str, note: str = "") -> None:
    await store.db.actions.insert_one({
        "ticket_id": ticket_id, "customer_id": None, "tool": tool, "args": {"note": note} if note else {}, "tier": "human",
        "decision": {"result": "allow", "rule_id": "human", "reason": f"by {name}"}, "status": "executed", "result": None,
        "idempotency_key": None, "decided_by": name, "decided_at": now(), "created_at": now(), "updated_at": now()})


async def _ensure_escalation(tid: ObjectId, name: str) -> None:
    """A human can take over or resolve a ticket that was never escalated; give it an escalation record first."""
    await store.db.tickets.update_one({"_id": tid, "escalation": None}, {"$set": {"escalation": {
        "reason": f"Taken over by {name}", "rule_id": "human_initiated", "note": "", "handoff_summary": None, "status": "open",
        "assignee": None, "taken_at": None, "at": now()}}})


@router.post("/tickets/{ticket_id}/takeover")
async def takeover(ticket_id: str, body: HumanIn) -> dict:
    name = _human(body.name)
    tid = _oid(ticket_id)
    await _ensure_escalation(tid, name)
    t = await store.db.tickets.find_one_and_update(
        {"_id": tid, "mode": "ai"}, {"$set": {"mode": "human", "updated_at": now(), "escalation.assignee": name,
                                              "escalation.taken_at": now(), "escalation.status": "taken"}},
        return_document=ReturnDocument.AFTER)
    if t is None:
        raise HTTPException(409, "ticket already taken over or missing")
    await _timeline_event(tid, "human_takeover", name)
    await core.deliver(tid, f"You're now chatting with {name} from our support team.", author="human", author_name=name)
    events.publish("takeover", tid, by=name)
    return {"mode": "human"}


@router.post("/tickets/{ticket_id}/reply")
async def human_reply(ticket_id: str, body: HumanIn) -> dict:
    name = _human(body.name)
    t = await store.db.tickets.find_one({"_id": _oid(ticket_id)})
    if t is None or t["mode"] != "human":
        raise HTTPException(409, "take over the ticket before replying")
    if not body.text.strip():
        raise HTTPException(400, "empty reply")
    msg = await core.deliver(t["_id"], body.text.strip()[: config.TEXT_REPLY_CAP], author="human", author_name=name)
    return {"message": jsonable(msg)}


@router.post("/tickets/{ticket_id}/resolve")
async def resolve(ticket_id: str, body: HumanIn) -> dict:
    name = _human(body.name)
    tid = _oid(ticket_id)
    await _ensure_escalation(tid, name)
    await core.deliver(tid, "Your case has been resolved. Thank you for your patience!", author="human", author_name=name)
    await tickets.set_status(tid, "resolved", mode="ai", resolution=body.note or f"Resolved by {name}", **{"escalation.status": "resolved"})
    await _timeline_event(tid, "human_resolve", name, body.note)
    return {"status": "resolved"}


@router.post("/tickets/{ticket_id}/handback")
async def handback(ticket_id: str, body: HumanIn) -> dict:
    name = _human(body.name)
    tid = _oid(ticket_id)
    t = await store.db.tickets.find_one_and_update(
        {"_id": tid, "mode": "human"}, {"$set": {"mode": "ai", "human_note": f"{name}: {body.note}" if body.note else None,
                                                 "escalation.status": "handed_back", "updated_at": now()}},
        return_document=ReturnDocument.AFTER)
    if t is None:
        raise HTTPException(409, "ticket is not in human mode")
    await tickets.set_status(tid, "open")
    await _timeline_event(tid, "human_handback", name, body.note)
    return {"mode": "ai"}


@router.get("/metrics")
async def metrics() -> dict:
    decided = [a async for a in store.db.actions.find({"decided_by": {"$in": config.HUMAN_AGENTS}, "tier": "approve"})]
    approved = [a for a in decided if a["status"] in ("executed", "blocked")]
    rejected = [a for a in decided if a["status"] == "rejected"]
    waits = [(a["decided_at"] - a["created_at"]).total_seconds() for a in decided if a.get("decided_at")]
    held = await store.db.actions.count_documents({"tier": "approve"})
    return {"held_for_approval": held, "approved": len(approved), "rejected": len(rejected),
            "approval_rate": round(len(approved) / len(decided), 2) if decided else None,
            "rejection_rate": round(len(rejected) / len(decided), 2) if decided else None,
            "median_time_to_decision_s": round(statistics.median(waits), 1) if waits else None,
            "takeovers": await store.db.actions.count_documents({"tool": "human_takeover"}),
            "escalations": await store.db.tickets.count_documents({"escalation": {"$ne": None}}),
            "blocked_actions": await store.db.actions.count_documents({"status": "blocked"})}
