"""The agent's 10 tools: Pydantic argument schemas, specs for every lane, and idempotent executors.

Every proposal goes through policy.decide() and is recorded in `actions` whether it runs or not.
Side-effect actions use the idempotency pattern: insert the action row with its key (the unique index
stops a duplicate), perform the side effects, then mark it executed.
"""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from bson import ObjectId
from bson.errors import InvalidId
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app import db as store
from app import events, tickets
from app.db import jsonable, now
from app.llm.base import ToolSpec
from app.llm.schema import strict_schema
from app.policy import Decision, PolicyState, decide


# ---------- argument schemas ----------

class VerifyIdentity(BaseModel):
    order_number: str = Field(description="Order number the customer gave, e.g. RA-10234")
    factor: str = Field(description="Email address on the order (chat, email, WhatsApp) or last 4 digits of the phone number (voice)")


class OrderRef(BaseModel):
    order_number: str = Field(description="Order number, e.g. RA-10234")


class IssueRefund(BaseModel):
    order_number: str
    amount: int = Field(description="Refund amount in whole rupees")
    reason: str = Field(description="Short reason, e.g. 'item arrived damaged'")


class OrderAction(BaseModel):
    order_number: str
    reason: str


class SendNotification(BaseModel):
    channel: Literal["email", "sms"]
    message: str = Field(description="Notification text in English")


class ConfirmAction(BaseModel):
    action_id: str = Field(description="action_id returned when the action was proposed")


class Escalate(BaseModel):
    reason: Literal["legal_threat", "fraud_signal", "distress", "customer_requested_human", "other"]
    note: str = Field(description="One line for the human teammate")


class Fact(BaseModel):
    kind: Literal["order_number", "amount", "date", "tracking_number"]
    value: str = Field(description="Exactly as written in reply_en")


class ReplyToCustomer(BaseModel):
    reply_en: str = Field(description="The reply, in English, in the channel's style")
    facts: list[Fact] = Field(description="Every order number, amount, date and tracking number in reply_en")
    sentiment: Literal["calm", "frustrated", "angry", "distressed"]
    confidence: Literal["low", "medium", "high"]
    escalation_reason: Literal["none", "legal_threat", "fraud_signal", "distress", "customer_requested_human"]


TOOL_MODELS: dict[str, tuple[str, type[BaseModel]]] = {
    "verify_identity": ("Verify who the customer is. The ONLY way to verify. Needs the order number plus the factor for this channel. "
                        "Ask the customer for both first; never guess them.", VerifyIdentity),
    "get_order": ("Get an order's status, items, total and refunds. Works only after verification.", OrderRef),
    "get_tracking": ("Get shipment tracking for an order: carrier, tracking number, status, ETA and scan events.", OrderRef),
    "issue_refund": ("Propose a refund in whole rupees through the payments system. The rules engine decides whether it waits for "
                     "the customer's yes or for a human approver. Never say a refund is done unless the result says executed.", IssueRefund),
    "create_reship": ("Propose a free replacement shipment for a damaged or missing item. Waits for the customer's yes.", OrderAction),
    "cancel_order": ("Propose cancelling an order that has not shipped yet. Waits for the customer's yes.", OrderAction),
    "send_notification": ("Send the customer an email or SMS outside this conversation (notifications system).", SendNotification),
    "confirm_action": ("Run a pending action after the customer clearly said yes to it in their NEW message. "
                       "Use the action_id from the proposal.", ConfirmAction),
    "escalate": ("Hand the ticket to a human teammate: legal threats, fraud signals, distress, or the customer asks for a human.", Escalate),
    "reply_to_customer": ("Send your reply and END the turn. Call it last, after every other tool result is back. "
                          "Write reply_en in English (it is translated automatically). List every order number, amount, "
                          "date and tracking number you mention in facts.", ReplyToCustomer),
}
TOOL_SPECS = [ToolSpec(name, desc, strict_schema(model)) for name, (desc, model) in TOOL_MODELS.items()]


# ---------- turn context ----------

@dataclass
class Ctx:
    ticket: dict[str, Any]
    identity: dict[str, Any]
    customer: dict[str, Any] | None
    channel: str
    message_at: datetime
    escalations: list[Decision] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return bool(self.identity.get("verified") and self.customer)


def normalize_order_number(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    return f"RA-{digits}" if digits else (raw or "").strip().upper()


async def policy_state(ctx: Ctx, order_number: str | None = None) -> PolicyState:
    s = PolicyState(verified=ctx.verified, tier=(ctx.customer or {}).get("tier", "standard"),
                    identity_attempts=ctx.identity.get("attempts", 0),
                    repeat_contacts_30d=await tickets.repeat_contacts_30d((ctx.customer or {}).get("_id")))
    if order_number:
        order = await store.db.orders.find_one({"order_number": normalize_order_number(order_number)})
        s.order_exists = order is not None
        if order and ctx.customer and order["customer_id"] == ctx.customer["_id"]:
            s.order = order
            s.prior_refunds = await store.db.refunds.count_documents({"order_id": order["_id"]})
    return s


async def record(ctx: Ctx, tool: str, args: dict, decision: Decision, status: str, result: Any = None,
                 key: str | None = None) -> dict[str, Any]:
    doc = {"_id": ObjectId(), "ticket_id": ctx.ticket["_id"], "customer_id": (ctx.customer or {}).get("_id"), "channel": ctx.channel,
           "tool": tool, "args": args, "tier": decision.result,
           "decision": {"result": decision.result, "rule_id": decision.rule_id, "reason": decision.reason},
           "status": status, "result": result, "idempotency_key": key, "decided_by": None, "decided_at": None,
           "decision_note": None, "created_at": now(), "updated_at": now()}
    await store.db.actions.insert_one(doc)
    events.publish("action", ctx.ticket["_id"], tool=tool, status=status, rule_id=decision.rule_id)
    return doc


# ---------- executors ----------

def order_view(o: dict[str, Any]) -> dict[str, Any]:
    ship = o.get("shipment") or {}
    return {"order_number": o["order_number"], "status": o["status"], "placed_at": o["placed_at"].date().isoformat(),
            "items": [{"name": i["name"], "price": i["price"], "qty": i["qty"]} for i in o["items"]],
            "total": o["total"], "refunded_so_far": o.get("refund_total", 0),
            "shipment": {"carrier": ship.get("carrier"), "status": ship.get("status"), "eta": ship.get("eta")} if ship else None}


def tracking_view(o: dict[str, Any]) -> dict[str, Any]:
    ship = o.get("shipment")
    if not ship:
        return {"order_number": o["order_number"], "shipped": False, "note": "Not shipped yet"}
    return {"order_number": o["order_number"], "carrier": ship["carrier"], "tracking_number": ship["tracking_no"],
            "status": ship["status"], "eta": ship["eta"],
            "events": [{"date": e["at"].date().isoformat(), "status": e["status"], "location": e["location"]} for e in ship["events"]]}


async def verify_identity(ctx: Ctx, a: VerifyIdentity) -> dict[str, Any]:
    if ctx.verified:
        return {"verified": True, "note": "Already verified", "customer_name": ctx.customer["name"]}
    state = await policy_state(ctx)
    decision = decide("verify_identity", a.model_dump(), state)
    if decision.result == "escalate":
        ctx.escalations.append(decision)
        return {"verified": False, "escalated": True}
    order = await store.db.orders.find_one({"order_number": normalize_order_number(a.order_number)})
    customer = await store.db.customers.find_one({"_id": order["customer_id"]}) if order else None
    factor = a.factor.strip().lower()
    if customer is None:
        ok = False
    elif ctx.channel == "voice":
        ok = re.sub(r"\D", "", factor)[-4:] == re.sub(r"\D", "", customer["phone"])[-4:] and len(re.sub(r"\D", "", factor)) >= 4
    else:
        ok = factor == customer["email"].lower()
    args = {"order_number": a.order_number, "factor": "***"}  # never store the factor
    if ok:
        ctx.identity = await tickets.auto_verify(ctx.identity, customer, "verify_identity")
        ctx.customer = customer
        ctx.ticket = await tickets.attach_customer(ctx.ticket, customer["_id"])
        await record(ctx, "verify_identity", args, decision, "executed", {"verified": True})
        await store.db.tickets.update_one({"_id": ctx.ticket["_id"]}, {"$addToSet": {"order_ids": order["_id"]}})
        # The order is preloaded here to save a model call (latency and free-tier quota); only after verification.
        return {"verified": True, "customer_name": customer["name"], "tier": customer["tier"],
                "order": order_view(order), "tracking": tracking_view(order)}
    ctx.identity = await store.db.identities.find_one_and_update(
        {"_id": ctx.identity["_id"]}, {"$inc": {"attempts": 1}}, return_document=ReturnDocument.AFTER)
    attempts = ctx.identity["attempts"]
    shape = "email" if "@" in factor else "digits" if re.fullmatch(r"[\d\s-]+", factor) else "other"
    await record(ctx, "verify_identity", args, Decision("deny", "verify_failed", "Order number and factor do not match"),
                 "blocked", {"verified": False, "attempts": attempts, "order_found": order is not None, "factor_shape": shape})
    if attempts >= 3:
        ctx.escalations.append(Decision("escalate", "identity_failed", "3 failed identity checks"))
        return {"verified": False, "attempts_left": 0, "escalated": True}
    return {"verified": False, "attempts_left": 3 - attempts}


async def read_order(ctx: Ctx, tool: str, a: OrderRef) -> dict[str, Any]:
    state = await policy_state(ctx, a.order_number)
    decision = decide(tool, a.model_dump(), state)
    if decision.result != "allow":
        await record(ctx, tool, a.model_dump(), decision, "blocked")
        return {"blocked": True, "rule_id": decision.rule_id, "reason": decision.reason}
    view = order_view(state.order) if tool == "get_order" else tracking_view(state.order)
    await store.db.tickets.update_one({"_id": ctx.ticket["_id"]}, {"$addToSet": {"order_ids": state.order["_id"]}})
    await record(ctx, tool, a.model_dump(), decision, "executed", {"order_number": view["order_number"]})
    return view


def idempotency_key(ctx: Ctx, tool: str, args: dict[str, Any]) -> str:
    order = normalize_order_number(args.get("order_number", ""))
    if tool == "issue_refund":
        return f"refund:{ctx.ticket['_id']}:{order}:{args['amount']}"
    if tool == "send_notification":
        return f"notify:{ctx.ticket['_id']}:{hashlib.sha1(args['message'].encode()).hexdigest()[:10]}"
    return f"{tool}:{ctx.ticket['_id']}:{order}"


async def propose(ctx: Ctx, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """issue_refund / create_reship / cancel_order / send_notification: decide, then hold or run."""
    state = await policy_state(ctx, args.get("order_number"))
    decision = decide(tool, args, state)
    if decision.result == "deny":
        await record(ctx, tool, args, decision, "blocked")
        return {"blocked": True, "rule_id": decision.rule_id, "reason": decision.reason}
    status = {"confirm": "pending_confirmation", "approve": "pending_approval", "allow": "executing"}[decision.result]
    key = idempotency_key(ctx, tool, args)
    try:
        action = await record(ctx, tool, args, decision, status, key=key)
    except DuplicateKeyError:
        existing = await store.db.actions.find_one({"idempotency_key": key})
        return {"action_id": str(existing["_id"]), "status": existing["status"], "note": "Same action already exists; not repeated",
                "result": jsonable(existing.get("result"))}
    if status == "executing":
        return await run_action(action)
    if status == "pending_approval":
        await tickets.set_status(ctx.ticket["_id"], "pending_approval")
        return {"action_id": str(action["_id"]), "status": status, "rule_id": decision.rule_id,
                "next": "Tell the customer a teammate is reviewing this request. Do not promise the outcome."}
    return {"action_id": str(action["_id"]), "status": status, "rule_id": decision.rule_id,
            "next": "Ask the customer to confirm. Call confirm_action with this action_id only after they say yes."}


async def run_action(action: dict[str, Any], approved: bool = False) -> dict[str, Any]:
    """Executor for an action already moved to `executing`. Re-checks deny rules against current data."""
    customer = await store.db.customers.find_one({"_id": action["customer_id"]})
    ticket = await store.db.tickets.find_one({"_id": action["ticket_id"]})
    identity = {"verified": True, "attempts": 0}
    ctx = Ctx(ticket=ticket, identity=identity, customer=customer, channel=action.get("channel", "web"), message_at=now())
    args = action["args"]
    state = await policy_state(ctx, args.get("order_number"))
    decision = decide(action["tool"], args, state, approved=approved)
    if decision.result == "deny" or (decision.result == "approve" and not approved):
        await finish(action, "blocked", {"rule_id": decision.rule_id, "reason": decision.reason}, decision)
        return {"blocked": True, "rule_id": decision.rule_id, "reason": decision.reason}
    order, tool = state.order, action["tool"]
    if tool == "issue_refund":
        try:
            await store.db.refunds.insert_one({"order_id": order["_id"], "ticket_id": ticket["_id"], "amount": args["amount"],
                                               "reason": args["reason"], "idempotency_key": action["idempotency_key"], "created_at": now()})
            await store.db.orders.update_one({"_id": order["_id"]}, {"$inc": {"refund_total": args["amount"]}})
        except DuplicateKeyError:
            pass
        result = {"refunded": args["amount"], "order_number": order["order_number"], "notified": True,
                  "systems": ["payments", "orders", "notifications"]}
    elif tool == "create_reship":
        reship_no = f"{order['order_number']}-R"
        if not await store.db.orders.find_one({"order_number": reship_no}):
            await store.db.orders.insert_one({"order_number": reship_no, "customer_id": order["customer_id"], "status": "processing",
                                              "items": order["items"], "total": 0, "placed_at": now(), "shipment": None,
                                              "refund_total": 0, "reship_of": order["_id"]})
        result = {"replacement_order": reship_no, "ships_in_days": 2, "notified": True, "systems": ["orders", "notifications"]}
    elif tool == "cancel_order":
        res = await store.db.orders.update_one({"_id": order["_id"], "status": "processing"}, {"$set": {"status": "cancelled"}})
        result = {"cancelled": bool(res.modified_count), "order_number": order["order_number"], "notified": True}
    elif tool == "send_notification":
        result = {"sent": True, "channel": args["channel"], "to": "customer on file"}
    else:
        result = {"note": "nothing to run"}
    await finish(action, "executed", result)
    return {"action_id": str(action["_id"]), "status": "executed", "result": result}


async def finish(action: dict[str, Any], status: str, result: Any, decision: Decision | None = None) -> None:
    update: dict[str, Any] = {"status": status, "result": result, "updated_at": now()}
    if decision:
        update["decision"] = {"result": decision.result, "rule_id": decision.rule_id, "reason": decision.reason}
    await store.db.actions.update_one({"_id": action["_id"]}, {"$set": update})
    events.publish("action", action["ticket_id"], tool=action["tool"], status=status)


async def confirm_action(ctx: Ctx, a: ConfirmAction) -> dict[str, Any]:
    if not ctx.verified:
        decision = decide("confirm_action", a.model_dump(), await policy_state(ctx))
        await record(ctx, "confirm_action", a.model_dump(), decision, "blocked")
        return {"blocked": True, "rule_id": decision.rule_id, "reason": decision.reason}
    try:
        action = await store.db.actions.find_one({"_id": ObjectId(a.action_id)})
    except InvalidId:
        action = None
    if action is None or action.get("customer_id") != ctx.customer["_id"]:
        return {"error": "No such pending action for this customer"}
    if action["status"] != "pending_confirmation":
        return {"error": f"Action is {action['status']}; only pending_confirmation actions can be confirmed by the customer"}
    if action["created_at"] >= ctx.message_at:
        return {"error": "The customer has not replied since this was proposed. Ask them to confirm first."}
    claimed = await store.db.actions.find_one_and_update(
        {"_id": action["_id"], "status": "pending_confirmation"},
        {"$set": {"status": "executing", "decided_by": "customer", "decided_at": now()}}, return_document=ReturnDocument.AFTER)
    if claimed is None:
        return {"error": "Action already handled"}
    return await run_action(claimed)


async def escalate(ctx: Ctx, a: Escalate) -> dict[str, Any]:
    reason = a.reason if a.reason != "other" else "agent_escalated"
    ctx.escalations.append(Decision("escalate", reason, a.note[:200]))
    return {"escalated": True, "next": "Tell the customer a teammate will take over shortly."}


async def execute(ctx: Ctx, name: str, args: BaseModel) -> dict[str, Any]:
    if name == "verify_identity":
        return await verify_identity(ctx, args)  # type: ignore[arg-type]
    if name in ("get_order", "get_tracking"):
        return await read_order(ctx, name, args)  # type: ignore[arg-type]
    if name in ("issue_refund", "create_reship", "cancel_order", "send_notification"):
        if name != "send_notification" and not ctx.verified:
            decision = decide(name, args.model_dump(), await policy_state(ctx))
            await record(ctx, name, args.model_dump(), decision, "blocked")
            return {"blocked": True, "rule_id": decision.rule_id, "reason": decision.reason}
        payload = args.model_dump()
        if "order_number" in payload:
            payload["order_number"] = normalize_order_number(payload["order_number"])
        return await propose(ctx, name, payload)
    if name == "confirm_action":
        return await confirm_action(ctx, args)  # type: ignore[arg-type]
    if name == "escalate":
        return await escalate(ctx, args)  # type: ignore[arg-type]
    return {"error": f"unknown tool {name}"}


async def check_facts(ctx: Ctx, reply: ReplyToCustomer) -> list[str]:
    """Every order number / tracking number / amount the reply cites must exist in the customer's records."""
    if not reply.facts:
        return []
    orders = [o async for o in store.db.orders.find({"customer_id": ctx.customer["_id"]})] if ctx.customer else []
    numbers = {o["order_number"] for o in orders}
    tracking = {(o.get("shipment") or {}).get("tracking_no") for o in orders} - {None}
    amounts = {o["total"] for o in orders} | {i["price"] for o in orders for i in o["items"]} | {o.get("refund_total", 0) for o in orders}
    async for act in store.db.actions.find({"ticket_id": ctx.ticket["_id"]}):
        if isinstance(act.get("args", {}).get("amount"), int):
            amounts.add(act["args"]["amount"])
    errors = []
    for f in reply.facts:
        if f.value not in reply.reply_en:
            errors.append(f"fact {f.value!r} does not appear verbatim in reply_en")
        elif not ctx.customer:
            continue  # unverified: no records to check against, and data tools were denied anyway
        elif f.kind == "order_number" and normalize_order_number(f.value) not in numbers:
            errors.append(f"order number {f.value} is not one of this customer's orders")
        elif f.kind == "tracking_number" and f.value.strip() not in tracking:
            errors.append(f"tracking number {f.value} not found")
        elif f.kind == "amount":
            digits = re.sub(r"[^\d.]", "", f.value)
            if digits and float(digits) not in {float(x) for x in amounts}:
                errors.append(f"amount {f.value} does not match any order, item or refund amount")
    return errors
