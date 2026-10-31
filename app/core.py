"""One core for every channel: handle() = dedupe, identity, ticket, lock, packet, tool loop, deliver."""
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from bson import ObjectId
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app import config, context, events, tickets
from app import db as store
from app.db import now
from app.lang import layer
from app.llm.base import LaneFailure, ToolResult
from app.llm.router import Lane, router
from app.policy import Decision, decide_reply
from app.tools import TOOL_MODELS, TOOL_SPECS, Ctx, ReplyToCustomer, check_facts, execute, policy_state

log = logging.getLogger("core")
MAX_STEPS = 6
FALLBACK_EN = "Sorry, I'm having trouble right now. A teammate will follow up with you shortly."
HANDOFF_EN = "I've passed your case to a teammate, who will follow up with you shortly."


@dataclass
class Inbound:
    channel: str  # web | voice | email | whatsapp
    external_id: str | None
    sender: str  # web session id, phone number or email address
    text: str
    modality: str = "text"  # text | voice
    language: str | None = None  # pinned by a selector or detected by STT
    thread_hint: str | None = None  # email message id to reply to
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Outcome:
    ticket_id: str | None
    message_id: str | None = None
    reply: str | None = None
    reply_en: str | None = None
    language: str = "en-IN"
    status: str = "open"
    duplicate: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


# Channel adapters register how to deliver an async message (approval outcome, human reply) to a customer.
Sender = Callable[[dict[str, Any], str, dict[str, Any]], Awaitable[None]]
SENDERS: dict[str, Sender] = {}


async def _auto_verify(inb: Inbound, identity: dict[str, Any]) -> dict[str, Any]:
    """A WhatsApp number or email sender that matches the customer record verifies on arrival."""
    if identity.get("verified") or inb.channel not in ("email", "whatsapp"):
        return identity
    query = {"email": inb.sender.lower()} if inb.channel == "email" else {"phone": inb.sender}
    customer = await store.db.customers.find_one(query)
    return await tickets.auto_verify(identity, customer, f"{inb.channel}_sender") if customer else identity


async def handle(inb: Inbound) -> Outcome:
    started = time.perf_counter()
    identity = await _auto_verify(inb, await tickets.get_identity(inb.channel, inb.sender))
    lang_in = await layer.to_english(inb.text, inb.language, inb.modality)
    ticket = await tickets.open_ticket(identity, inb.channel, lang_in.language)
    async with tickets.lock_for(ticket["_id"]):
        ticket = await store.db.tickets.find_one({"_id": ticket["_id"]})
        msg = {"_id": ObjectId(), "ticket_id": ticket["_id"], "author": "customer", "author_name": None, "channel": inb.channel,
               "modality": inb.modality, "language": lang_in.language, "text": inb.text, "text_en": lang_in.text_en,
               "external_id": inb.external_id, "sender": inb.sender,
               "meta": {"thread_hint": inb.thread_hint, **lang_in.meta, **inb.meta}, "created_at": now()}
        try:
            await store.db.messages.insert_one(msg)
        except DuplicateKeyError:
            return Outcome(ticket_id=str(ticket["_id"]), duplicate=True, status=ticket["status"])
        language = lang_in.language if lang_in.language in config.LANGUAGES else "en-IN"
        await store.db.tickets.update_one({"_id": ticket["_id"]}, {"$set": {"language": language, "updated_at": now()}})
        events.publish("message", ticket["_id"], session=inb.sender, author="customer", text_en=lang_in.text_en, channel=inb.channel)
        if ticket["mode"] == "human":
            return Outcome(ticket_id=str(ticket["_id"]), status=ticket["status"], meta={"mode": "human"})

        customer = await store.db.customers.find_one({"_id": identity["customer_id"]}) if identity.get("customer_id") else None
        ctx = Ctx(ticket=ticket, identity=identity, customer=customer, channel=inb.channel, message_at=msg["created_at"])
        reply, lane_name = await agent_turn(ctx, lang_in.text_en)
        escalated = await apply_escalations(ctx, reply)
        reply_en = reply.reply_en if reply else FALLBACK_EN
        if escalated and reply and reply.escalation_reason == "none" and not any(d.rule_id.startswith("escalate_") for d in ctx.escalations):
            reply_en = f"{reply_en} {HANDOFF_EN}"
        cap = config.VOICE_REPLY_CAP if inb.modality == "voice" else config.TEXT_REPLY_CAP
        reply_en = reply_en[:cap]
        facts = [f.value for f in reply.facts] if reply else []
        out = await store_reply(ctx.ticket, reply_en, inb.channel, inb.modality, facts, author="ai", session=inb.sender,
                                meta={"lane": lane_name, "latency_s": round(time.perf_counter() - started, 2)})
        await settle_status(ctx.ticket["_id"])
        asyncio.create_task(context.maybe_fold(ctx.ticket["_id"]))
        status = (await store.db.tickets.find_one({"_id": ctx.ticket["_id"]}))["status"]
        return Outcome(ticket_id=str(ctx.ticket["_id"]), message_id=str(out["_id"]), reply=out["text"], reply_en=reply_en,
                       language=out["language"], status=status, meta={"lane": lane_name, **out["meta"]})


async def agent_turn(ctx: Ctx, text_en: str) -> tuple[ReplyToCustomer | None, str | None]:
    """Try lanes in order; a failure restarts the whole turn on the next lane (idempotency keys stop repeats)."""
    for lane in router.lanes():
        try:
            return await run_on_lane(lane, ctx, text_en), lane.name
        except LaneFailure as f:
            log.warning("lane %s failed: %s", lane.name, f)
            lane.record_failure(f)
            events.publish("lane", ctx.ticket["_id"], lane=lane.name, error=str(f)[:120])
    ctx.escalations.append(Decision("escalate", "all_lanes_failed", "Every LLM lane failed"))
    return None, None


async def run_on_lane(lane: Lane, ctx: Ctx, text_en: str) -> ReplyToCustomer:
    system, user = await context.build(ctx, text_en)
    session = lane.adapter.session(system, user, TOOL_SPECS)
    results: list[ToolResult] | None = None
    repairs = 0
    for _ in range(MAX_STEPS):
        calls = await router.call(lane, lambda: session.step(results))
        await store.db.tickets.update_one({"_id": ctx.ticket["_id"]}, {"$inc": {"model_calls": 1}})
        results, reply, bad = [], None, False
        others = [c for c in calls if c.name != "reply_to_customer"]
        for call in calls:
            spec = TOOL_MODELS.get(call.name)
            if spec is None:
                bad = True
                results.append(ToolResult(call, {"error": f"Unknown tool {call.name}"}))
                continue
            try:
                args = spec[1].model_validate(call.args)
            except ValidationError as e:
                bad = True
                results.append(ToolResult(call, {"error": "Invalid arguments", "details": e.errors(include_url=False)[:3]}))
                continue
            if call.name == "reply_to_customer":
                if others:
                    results.append(ToolResult(call, {"error": "Not sent. Read the other tool results first, then call reply_to_customer alone."}))
                    continue
                problems = await check_facts(ctx, args)
                if problems:
                    bad = True
                    results.append(ToolResult(call, {"error": "Facts do not match the records", "problems": problems}))
                    continue
                reply = args
                results.append(ToolResult(call, {"ok": True}))
            else:
                results.append(ToolResult(call, await execute(ctx, call.name, args)))
        if reply is not None:
            return reply
        if bad:
            repairs += 1
            if repairs > 1:
                raise LaneFailure("invalid", detail="invalid tool call after one repair")
    raise LaneFailure("invalid", detail=f"no reply_to_customer within {MAX_STEPS} steps")


async def apply_escalations(ctx: Ctx, reply: ReplyToCustomer | None) -> bool:
    decisions = list(ctx.escalations)
    if reply is not None:
        state = await policy_state(ctx)
        d = decide_reply(reply.sentiment, reply.confidence, reply.escalation_reason, state)
        if d:
            decisions.append(d)
    if not decisions:
        return False
    first = decisions[0]
    if await tickets.escalate(ctx.ticket["_id"], first, note=first.reason):
        from app.api.handoff import schedule_handoff_summary
        schedule_handoff_summary(ctx.ticket["_id"])
    return True


async def settle_status(ticket_id: ObjectId) -> None:
    t = await store.db.tickets.find_one({"_id": ticket_id})
    if t["status"] in ("escalated", "resolved", "merged"):
        return
    if await store.db.actions.find_one({"ticket_id": ticket_id, "status": "pending_approval"}):
        status = "pending_approval"
    elif await store.db.actions.find_one({"ticket_id": ticket_id, "status": "pending_confirmation"}):
        status = "waiting_customer"
    else:
        status = "open"
    if status != t["status"]:
        await tickets.set_status(ticket_id, status)


async def store_reply(ticket: dict[str, Any], text_en: str, channel: str, modality: str, facts: list[str], author: str,
                      session: str | None, author_name: str | None = None, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Translate an English reply into the ticket's language, store it, publish it."""
    out = await layer.from_english(text_en, ticket.get("language") or "en-IN", modality, facts)
    msg = {"_id": ObjectId(), "ticket_id": ticket["_id"], "author": author, "author_name": author_name, "channel": channel,
           "modality": modality, "language": out.language, "text": out.text, "text_en": text_en, "external_id": None,
           "meta": {**(meta or {}), **out.meta}, "created_at": now()}
    await store.db.messages.insert_one(msg)
    events.publish("message", ticket["_id"], session=session, author=author, author_name=author_name, text=out.text,
                   text_en=text_en, channel=channel, message_id=str(msg["_id"]), language=out.language)
    return msg


async def deliver(ticket_id: ObjectId, text_en: str, author: str, author_name: str | None = None) -> dict[str, Any] | None:
    """Async delivery (approval outcome, human reply) on the customer's latest channel, in their language."""
    ticket = await store.db.tickets.find_one({"_id": ticket_id})
    last = await store.db.messages.find_one({"ticket_id": ticket_id, "author": "customer"}, sort=[("created_at", -1)])
    if ticket is None or last is None:
        return None
    msg = await store_reply(ticket, text_en, last["channel"], "text", [], author=author, session=last.get("sender"), author_name=author_name)
    sender = SENDERS.get(last["channel"])
    if sender:
        try:
            await sender(ticket, msg["text"], last)
        except Exception as e:  # noqa: BLE001
            log.error("delivery on %s failed: %s", last["channel"], e)
            await store.db.messages.update_one({"_id": msg["_id"]}, {"$set": {"meta.delivery_failed": str(e)[:200]}})
    return msg
