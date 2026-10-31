"""Ticket and identity helpers shared by core, tools and the dashboard API."""
import asyncio
from datetime import timedelta
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument

from app import db as store
from app import events
from app.db import now
from app.policy import Decision

OPEN_STATUSES = ["open", "waiting_customer", "pending_approval", "escalated"]
_locks: dict[str, asyncio.Lock] = {}


def lock_for(ticket_id: ObjectId) -> asyncio.Lock:
    return _locks.setdefault(str(ticket_id), asyncio.Lock())


def new_ticket(customer_id: ObjectId | None, identity_id: ObjectId, channel: str, language: str) -> dict[str, Any]:
    t = now()
    return {"_id": ObjectId(), "customer_id": customer_id, "identity_id": identity_id, "status": "open", "mode": "ai",
            "language": language, "channels": [channel], "order_ids": [], "summary": "", "summary_upto": None,
            "summary_version": 0, "resolution": None, "human_note": None, "escalation": None, "model_calls": 0,
            "created_at": t, "updated_at": t}


async def get_identity(channel: str, key: str) -> dict[str, Any]:
    return await store.db.identities.find_one_and_update(
        {"channel": channel, "key": key},
        {"$setOnInsert": {"customer_id": None, "verified": False, "verified_via": None, "attempts": 0, "created_at": now()}},
        upsert=True, return_document=ReturnDocument.AFTER)


async def auto_verify(identity: dict[str, Any], customer: dict[str, Any], via: str) -> dict[str, Any]:
    return await store.db.identities.find_one_and_update(
        {"_id": identity["_id"]}, {"$set": {"customer_id": customer["_id"], "verified": True, "verified_via": via}},
        return_document=ReturnDocument.AFTER)


async def open_ticket(identity: dict[str, Any], channel: str, language: str) -> dict[str, Any]:
    """The customer's one open ticket (shared across channels), or a temporary one for an unverified identity."""
    if identity.get("customer_id"):
        query: dict[str, Any] = {"customer_id": identity["customer_id"], "status": {"$in": OPEN_STATUSES}}
    else:
        query = {"identity_id": identity["_id"], "customer_id": None, "status": {"$in": OPEN_STATUSES}}
    ticket = await store.db.tickets.find_one(query, sort=[("updated_at", -1)])
    if ticket is None:
        ticket = new_ticket(identity.get("customer_id"), identity["_id"], channel, language)
        await store.db.tickets.insert_one(ticket)
        events.publish("ticket", ticket["_id"], status="open")
    elif channel not in ticket["channels"]:
        ticket = await store.db.tickets.find_one_and_update(
            {"_id": ticket["_id"]}, {"$addToSet": {"channels": channel}}, return_document=ReturnDocument.AFTER)
    return ticket


async def attach_customer(ticket: dict[str, Any], customer_id: ObjectId) -> dict[str, Any]:
    """On verification: merge a temporary ticket into the customer's open ticket, or adopt it."""
    target = await store.db.tickets.find_one(
        {"customer_id": customer_id, "status": {"$in": OPEN_STATUSES}, "_id": {"$ne": ticket["_id"]}}, sort=[("updated_at", -1)])
    if target is None:
        return await store.db.tickets.find_one_and_update(
            {"_id": ticket["_id"]}, {"$set": {"customer_id": customer_id, "updated_at": now()}}, return_document=ReturnDocument.AFTER)
    await store.db.messages.update_many({"ticket_id": ticket["_id"]}, {"$set": {"ticket_id": target["_id"]}})
    await store.db.actions.update_many({"ticket_id": ticket["_id"]}, {"$set": {"ticket_id": target["_id"]}})
    await store.db.tickets.update_one({"_id": ticket["_id"]}, {"$set": {"status": "merged", "merged_into": target["_id"]}})
    merged = await store.db.tickets.find_one_and_update(
        {"_id": target["_id"]}, {"$addToSet": {"channels": {"$each": ticket["channels"]}}, "$set": {"updated_at": now()}},
        return_document=ReturnDocument.AFTER)
    events.publish("ticket", target["_id"], merged_from=ticket["_id"])
    return merged


async def repeat_contacts_30d(customer_id: ObjectId | None) -> int:
    if customer_id is None:
        return 0
    return await store.db.tickets.count_documents(
        {"customer_id": customer_id, "status": {"$ne": "merged"}, "created_at": {"$gte": now() - timedelta(days=30)}})


async def set_status(ticket_id: ObjectId, status: str, **fields: Any) -> None:
    await store.db.tickets.update_one({"_id": ticket_id}, {"$set": {"status": status, "updated_at": now(), **fields}})
    events.publish("ticket", ticket_id, status=status)


async def escalate(ticket_id: ObjectId, decision: Decision, note: str = "") -> bool:
    """Move a ticket to escalated (no action taken). Returns False if it already was."""
    res = await store.db.tickets.update_one(
        {"_id": ticket_id, "status": {"$ne": "escalated"}},
        {"$set": {"status": "escalated", "updated_at": now(), "escalation": {
            "reason": decision.reason, "rule_id": decision.rule_id, "note": note, "handoff_summary": None,
            "status": "open", "assignee": None, "taken_at": None, "at": now()}}})
    if res.modified_count:
        await store.db.actions.insert_one({
            "ticket_id": ticket_id, "tool": "escalate", "args": {"note": note}, "tier": "escalate",
            "decision": {"result": "escalate", "rule_id": decision.rule_id, "reason": decision.reason},
            "status": "executed", "result": {"escalated": True}, "idempotency_key": None, "created_at": now(), "updated_at": now()})
        events.publish("escalated", ticket_id, rule_id=decision.rule_id, reason=decision.reason)
    return bool(res.modified_count)
