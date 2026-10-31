"""Handoff summary for the human desk: one strict-schema call on the light lanes, in the background."""
import asyncio
import logging
from typing import Any

from bson import ObjectId
from pydantic import BaseModel

from app import db as store
from app import events
from app.context import _msg_line

log = logging.getLogger("handoff")
LIMITS = {"issue": 200, "tried": 300, "mood": 40, "next_step": 200}


class HandoffSummary(BaseModel):
    issue: str
    tried: str
    mood: str
    next_step: str


async def write_handoff_summary(ticket_id: ObjectId) -> dict[str, Any] | None:
    from app.llm.router import router
    t = await store.db.tickets.find_one({"_id": ticket_id})
    msgs = [m async for m in store.db.messages.find({"ticket_id": ticket_id}).sort("created_at", -1).limit(12)]
    acts = [a async for a in store.db.actions.find({"ticket_id": ticket_id}).sort("created_at", 1)]
    prompt = ("Write a handoff note for a human support agent taking over this ticket. Plain English, short.\n"
              "Fields: issue (what the customer needs), tried (what the AI agent already did), mood (one or two words), "
              "next_step (suggested next step for the human).\n\n"
              f"Escalation: {(t.get('escalation') or {}).get('rule_id')} - {(t.get('escalation') or {}).get('reason')}\n"
              f"Earlier summary: {t.get('summary') or '(none)'}\n"
              "Actions: " + "; ".join(f"{a['tool']} {a['args']} -> {a['status']} ({a['decision']['rule_id']})" for a in acts) + "\n"
              "Messages (oldest first):\n" + "\n".join(_msg_line(m) for m in reversed(msgs)))
    try:
        s = await router.structured(prompt, HandoffSummary)
    except Exception as e:  # noqa: BLE001
        log.warning("handoff summary failed: %s", e)
        return None
    summary = {k: str(getattr(s, k))[:n] for k, n in LIMITS.items()}  # length limits enforced in code
    await store.db.tickets.update_one({"_id": ticket_id}, {"$set": {"escalation.handoff_summary": summary}})
    events.publish("handoff", ticket_id, summary=summary)
    return summary


def schedule_handoff_summary(ticket_id: ObjectId) -> None:
    asyncio.create_task(write_handoff_summary(ticket_id))
