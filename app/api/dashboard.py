"""Dashboard API: tickets, timeline, Needs-attention queue, approvals (compare-and-set) and takeover."""
import statistics
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pymongo import ReturnDocument

from app import config, core, events, grants, registry, tickets
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
    """Reviewers and admins may decide cases. Demo-level roles: a label, not authentication."""
    if name not in config.HUMAN_AGENTS:
        raise HTTPException(403, f"{name!r} is not a reviewer or admin")
    return name


def _admin(name: str) -> str:
    if name not in config.ADMINS:
        raise HTTPException(403, f"{name!r} is not an admin; only admins change tool grants")
    return name


async def _customers(ids: list[Any]) -> dict[Any, dict[str, Any]]:
    return {c["_id"]: c async for c in store.db.customers.find({"_id": {"$in": [i for i in ids if i]}})}


@router.get("/config")
async def ui_config() -> dict:
    rt = registry.RUNTIME
    labels = {"subject": "customer", "primary": "orders", "money": "refunds", "title": "Support desk"}
    pack_label = rt.pack.labels.get("reviewer", "reviewer") if rt else "reviewer"  # e.g. "merchant" for the kirana pack
    roles = {n: "admin" if n in config.ADMINS else (pack_label if rt and n in rt.pack.reviewers else "reviewer") for n in config.HUMAN_AGENTS}
    if rt is None:
        return {"product": config.PRODUCT_NAME, "humans": config.HUMAN_AGENTS, "roles": roles, "languages": config.LANGUAGES,
                "pack": None, "labels": labels, "tool_labels": {}, "features": [], "chat_url": "/static/chat.html", "chat_label": "Customer chat"}
    paytm = "paytm" in rt.pack.channels
    return {"product": rt.pack.product, "humans": config.HUMAN_AGENTS, "roles": roles, "languages": config.LANGUAGES, "pack": rt.pack.name,
            "labels": labels | {"title": "Desk"} | rt.pack.labels, "tool_labels": rt.tool_labels(),
            "features": ["paytm_insights"] if "issue_coupon" in rt.plugins else [],
            "chat_url": "/paytm" if paytm else "/static/chat.html",
            "chat_label": "Paytm chat (simulated)" if paytm else f"{rt.pack.labels.get('subject', 'customer').capitalize()} chat"}


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
    if registry.RUNTIME and action["tool"] in registry.RUNTIME.plugins:
        inner = result.get("result") or {}
        if isinstance(inner, dict) and inner.get("customer_message"):  # a proactive offer carries its own message
            return inner["customer_message"]
        return f"Good news: a staff member approved your {registry.RUNTIME.plugins[action['tool']].label.lower()} request. It has been processed."
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
        proactive = bool((claimed.get("args") or {}).get("proactive"))  # an insight offer the customer never asked for
        if not (proactive and result.get("blocked")):  # a blocked offer was never promised, so there is nothing to tell them
            await core.deliver(claimed["ticket_id"], _outcome_text(claimed, result), author="ai")
        await core.settle_status(claimed["ticket_id"])
        if proactive and not result.get("blocked"):
            await tickets.set_status(claimed["ticket_id"], "waiting_customer")
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
    proactive = bool((claimed.get("args") or {}).get("proactive"))
    async with tickets.lock_for(claimed["ticket_id"]):
        if not proactive:
            await core.deliver(claimed["ticket_id"], f"A teammate reviewed your request and couldn't approve it.{note}", author="ai")
        elif not await store.db.messages.count_documents({"ticket_id": claimed["ticket_id"], "author": "customer"}):
            # a rejected insight offer the customer never saw: no notice, and the empty ticket closes
            await tickets.set_status(claimed["ticket_id"], "resolved", resolution=f"Insight offer rejected by {name}")
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


# ---------- tool catalog and grants (admins only for changes) ----------

class GrantIn(BaseModel):
    name: str
    tier: str | None = None
    max_per_day: int | None = None
    clear_cap: bool = False


@router.get("/tools")
async def tools_view() -> dict:
    g = await grants.current()
    tools = [{"name": m.name, "label": m.label or m.name, "description": m.description, "effect": m.effect,
              "loosest_tier": m.loosest_tier, "source": m.source,
              "secrets": [{"name": s, "present": bool(getattr(config, s, None))} for s in m.secrets],
              "grant": jsonable({k: v for k, v in g.get(m.name, {}).items() if k != "_id"}) or None}
             for m in grants.catalog().values()]
    audit = [a async for a in store.db.grant_audit.find({"pack": grants.pack_name()}, {"_id": 0}).sort("at", -1).limit(20)]
    outbox = [o async for o in store.db.outbox.find({}, {"_id": 0}).sort("created_at", -1).limit(10)]
    return {"pack": grants.pack_name(), "tools": tools, "audit": jsonable(audit), "outbox": jsonable(outbox),
            "note": "Demo-level roles; real login is roadmap. Admins never upload code: plugins are written and deployed by developers."}


@router.post("/tools/{tool}/grant")
async def grant_tool(tool: str, body: GrantIn) -> dict:
    who = _admin(body.name)
    if tool not in grants.catalog():
        raise HTTPException(404, f"{tool} is not in this teammate's catalog")
    error = grants.validate_change(tool, body.tier, body.max_per_day)
    if error:
        raise HTTPException(422, error)
    return jsonable({k: v for k, v in (await grants.change(tool, who, True, body.tier, body.max_per_day, body.clear_cap)).items() if k != "_id"})


@router.post("/tools/{tool}/revoke")
async def revoke_tool(tool: str, body: GrantIn) -> dict:
    who = _admin(body.name)
    if tool not in grants.catalog():
        raise HTTPException(404, f"{tool} is not in this teammate's catalog")
    return jsonable({k: v for k, v in (await grants.change(tool, who, False)).items() if k != "_id"})
