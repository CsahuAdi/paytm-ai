"""Runs a config-only pack on the engine: tool models from specs, generic verification, db tools, data rules.

Reuses the engine's audit log (tools.record/finish), confirmation flow (tools.confirm_action), escalation and the
idempotency index. Personal collections are always scoped to the verified subject, whatever a template says.
"""
import json
import re
from typing import Annotated, Any, Literal

from bson import ObjectId
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, create_model
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app import config, tickets
from app import db as store
from app.db import jsonable, now
from app.llm.base import ToolSpec
from app.llm.schema import strict_schema
from app.packs import expr
from app.packs.spec import Pack, Param, ToolDef
from app.policy import Decision
from app.tools import TOOL_MODELS as CS_TOOLS
from app.tools import ConfirmAction, Ctx, SendNotification, confirm_action, escalate, finish, propose, record

MODEL_ONLY = {"pattern", "maxLength", "minLength", "minimum", "maximum"}  # enforced by Pydantic, hidden from LLM schemas


def no_operator(v: Any) -> Any:
    if isinstance(v, str) and (v.lstrip().startswith("$") or "{" in v or "}" in v):
        raise ValueError("value may not start with '$' or contain braces")
    return v


SafeStr = Annotated[str, AfterValidator(no_operator)]


def _field(p: Param) -> tuple[Any, Any]:
    if p.enum:
        return Literal[tuple(p.enum)], Field(description=p.description)  # type: ignore[valid-type]
    if p.type == "string":
        return SafeStr, Field(description=p.description, max_length=p.max_length, pattern=p.pattern)
    if p.type == "integer":
        return int, Field(description=p.description, ge=p.minimum, le=p.maximum)
    if p.type == "number":
        return float, Field(description=p.description, ge=p.minimum, le=p.maximum)
    return bool, Field(description=p.description)


def build_model(name: str, params: dict[str, Param]) -> type[BaseModel]:
    """The validation model is generated from the spec; strict mode means no coercion of dicts or lists into strings."""
    return create_model(f"P_{name}", __config__=ConfigDict(extra="forbid", strict=True),  # type: ignore[call-overload]
                        **{k: _field(p) for k, p in params.items()})


def _llm_schema(model: type[BaseModel]) -> dict:
    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in MODEL_ONLY}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node
    return strip(strict_schema(model))


class Runtime:
    def __init__(self, pack: Pack) -> None:
        self.pack = pack
        self.tools: dict[str, ToolDef] = {t.name: t for t in pack.tools}
        v = pack.verification
        verify = create_model("VerifyIdentity", __config__=ConfigDict(extra="forbid"), **{  # type: ignore[call-overload]
            v.param: (SafeStr, Field(description=v.param_description, max_length=40)),
            "factor": (SafeStr, Field(description=v.factor_description, max_length=80))})
        fact = create_model("Fact", kind=(Literal[tuple(pack.facts)], ...),  # type: ignore[valid-type]
                            value=(str, Field(description="Exactly as written in reply_en")))
        reply = create_model("ReplyToCustomer", __base__=CS_TOOLS["reply_to_customer"][1], facts=(list[fact], ...),  # type: ignore[valid-type]
                             escalation_reason=(Literal[("none", *pack.escalation_reasons)], ...))  # type: ignore[valid-type]
        esc = create_model("Escalate", reason=(Literal[(*pack.escalation_reasons, "other")], ...), note=(str, ...))  # type: ignore[valid-type]
        subject = pack.labels.get("subject", "customer")
        self.models: dict[str, tuple[str, type[BaseModel]]] = {
            "verify_identity": (f"Verify who the {subject} is. The ONLY way to verify. Needs {v.param} plus the factor. "
                                "Ask for both first; never guess them.", verify),
            **{t.name: (t.description, build_model(t.name, t.params)) for t in pack.tools},
            "send_notification": (CS_TOOLS["send_notification"][0], SendNotification),
            "confirm_action": (CS_TOOLS["confirm_action"][0].replace("customer", subject), ConfirmAction),
            "escalate": ("Hand the conversation to a human for: " + ", ".join(pack.escalation_reasons) + ".", esc),
            "reply_to_customer": (CS_TOOLS["reply_to_customer"][0], reply),
        }
        self.specs = [ToolSpec(n, d, _llm_schema(m)) for n, (d, m) in self.models.items()]

    # ---------- scoping ----------

    def scoped_filter(self, collection: str, template: dict[str, Any], scopes: dict[str, Any]) -> dict[str, Any]:
        f = expr.fill(template, scopes)
        owner = self.pack.collections[collection].get("owner_field")
        if owner:  # personal entity: always the verified subject's own records, regardless of the template
            f[owner] = scopes["session"]["subject_id"]
        return f

    def scopes(self, ctx: Ctx, params: dict[str, Any]) -> dict[str, Any]:
        subject_id = (ctx.customer or {}).get(self.pack.verification.lookup["field"]) if ctx.verified else None
        return {"params": params, "session": {"subject_id": subject_id, "channel": ctx.channel, "verified": ctx.verified}}

    # ---------- execution ----------

    async def execute(self, ctx: Ctx, name: str, args: BaseModel) -> dict[str, Any]:
        params = args.model_dump()
        if name == "verify_identity":
            return await self.verify(ctx, params)
        if name in ("send_notification", "confirm_action", "escalate"):
            if name == "escalate":
                return await escalate(ctx, args)  # type: ignore[arg-type]
            if not ctx.verified:
                d = Decision("deny", "unverified", "Identity not verified")
                await record(ctx, name, params, d, "blocked")
                return {"blocked": True, "rule_id": d.rule_id}
            return await (confirm_action(ctx, args) if name == "confirm_action" else propose(ctx, name, params))  # type: ignore[arg-type]
        tool = self.tools[name]
        if not ctx.verified and not tool.public:
            d = Decision("deny", "unverified", "Identity not verified; data and action tools are blocked")
            await record(ctx, name, params, d, "blocked")
            return {"blocked": True, "rule_id": d.rule_id, "reason": d.reason}
        scopes = self.scopes(ctx, params)
        if tool.effect == "read":
            projection = {f: 1 for f in tool.db.fields} | {"_id": 0}
            docs = [d async for d in store.db[tool.db.collection].find(
                self.scoped_filter(tool.db.collection, tool.db.filter, scopes), projection).limit(tool.db.limit)]
            await record(ctx, name, params, Decision("allow", "read", "Allowed"), "executed", {"count": len(docs)})
            return {"results": jsonable(docs)}
        target = await store.db[tool.db.collection].find_one(self.scoped_filter(tool.db.collection, tool.db.filter, scopes))
        decision = self.decide(tool, scopes | {tool.db.as_: target})
        if decision.result == "deny":
            await record(ctx, name, params, decision, "blocked")
            return {"blocked": True, "rule_id": decision.rule_id, "reason": decision.reason}
        status = {"confirm": "pending_confirmation", "approve": "pending_approval", "allow": "executing"}[decision.result]
        key = f"{self.pack.name}:{ctx.ticket['_id']}:{expr.fill(tool.idempotency, scopes | {tool.db.as_: target})}"
        try:
            action = await record(ctx, name, params, decision, status, key=key)
        except DuplicateKeyError:
            existing = await store.db.actions.find_one({"idempotency_key": key})
            return {"action_id": str(existing["_id"]), "status": existing["status"], "note": "Same action already exists; not repeated"}
        if status == "executing":
            return await self.run(action)
        if status == "pending_approval":
            await tickets.set_status(ctx.ticket["_id"], "pending_approval")
            return {"action_id": str(action["_id"]), "status": status, "rule_id": decision.rule_id,
                    "next": "Tell them a staff member is reviewing this request. Do not promise the outcome."}
        return {"action_id": str(action["_id"]), "status": status, "rule_id": decision.rule_id,
                "next": "Ask them to confirm. Call confirm_action with this action_id only after they say yes."}

    def decide(self, tool: ToolDef, scopes: dict[str, Any], approved: bool = False) -> Decision:
        if scopes.get(tool.db.as_) is None:
            return Decision("deny", "not_found", "No such record for this member")
        rule = tool.rule
        if rule is None or (rule.tier is None and not rule.tiers):
            return Decision("deny", "no_tier_declared", "Write tools without a declared tier are denied")
        for d in rule.deny_if:  # hard: approval never lifts these
            if expr.holds(d.when, scopes):
                return Decision("deny", d.rule_id, d.reason)
        for t in rule.tiers:
            if expr.holds(t.when, scopes):
                if t.tier == "approve" and approved:
                    return Decision("allow", "approved", "Approved by a human")
                return Decision(t.tier, t.rule_id, t.reason or t.rule_id)
        if rule.tier is None:
            return Decision("deny", "no_tier_declared", "No tier matched and no default tier is declared")
        return Decision(rule.tier, rule.rule_id, "Pack rule")

    async def run(self, action: dict[str, Any], approved: bool = False) -> dict[str, Any]:
        """Executor for an action already moved to `executing`: re-check deny rules on fresh data, then write once."""
        tool = self.tools[action["tool"]]
        customer = await store.db.customers.find_one({"_id": action["customer_id"]})
        subject_id = customer.get(self.pack.verification.lookup["field"]) if customer else None
        scopes = {"params": action["args"], "session": {"subject_id": subject_id, "verified": customer is not None}}
        target = await store.db[tool.db.collection].find_one(self.scoped_filter(tool.db.collection, tool.db.filter, scopes))
        decision = self.decide(tool, scopes | {tool.db.as_: target}, approved=approved)
        if decision.result == "deny" or (decision.result == "approve" and not approved):
            await finish(action, "blocked", {"rule_id": decision.rule_id, "reason": decision.reason}, decision)
            return {"blocked": True, "rule_id": decision.rule_id, "reason": decision.reason}
        update: dict[str, Any] = {}
        if tool.db.set:
            update["$set"] = expr.fill(tool.db.set, scopes | {tool.db.as_: target})
        if tool.db.inc:
            update["$inc"] = dict(tool.db.inc)
        after = await store.db[tool.db.collection].find_one_and_update({"_id": target["_id"]}, update, return_document=ReturnDocument.AFTER)
        result = jsonable(expr.fill(tool.result, scopes | {tool.db.as_: target, "after": after})) if tool.result else {"ok": True}
        await finish(action, "executed", result)
        return {"action_id": str(action["_id"]), "status": "executed", "result": result}

    async def verify(self, ctx: Ctx, params: dict[str, Any]) -> dict[str, Any]:
        v = self.pack.verification
        if ctx.verified:
            return {"verified": True, "note": "Already verified", "name": ctx.customer["name"]}
        if ctx.identity.get("attempts", 0) >= config.MAX_IDENTITY_ATTEMPTS:
            ctx.escalations.append(Decision("escalate", "identity_failed", "3 failed identity checks"))
            return {"verified": False, "escalated": True}
        key = str(params[v.param]).strip().upper()
        subject = await store.db[v.lookup["collection"]].find_one({v.lookup["field"]: key})
        if subject is not None and v.lookup.get("subject_ref"):
            subject = await store.db.customers.find_one({"_id": subject[v.lookup["subject_ref"]]})
        kind = v.factors.get("voice" if ctx.channel == "voice" else "text", "email")
        factor = params["factor"].strip().lower()
        if subject is None:
            ok = False
        elif kind == "phone_last4":
            digits = re.sub(r"\D", "", factor)
            ok = len(digits) >= 4 and digits[-4:] == re.sub(r"\D", "", subject["phone"])[-4:]
        else:
            ok = factor == subject["email"].lower()
        args = {v.param: key, "factor": "***"}
        if ok:
            ctx.identity = await tickets.auto_verify(ctx.identity, subject, "verify_identity")
            ctx.customer = subject
            ctx.ticket = await tickets.attach_customer(ctx.ticket, subject["_id"])
            await record(ctx, "verify_identity", args, Decision("allow", "verify", "Identity check allowed"), "executed", {"verified": True})
            return {"verified": True, "name": subject["name"]}
        ctx.identity = await store.db.identities.find_one_and_update(
            {"_id": ctx.identity["_id"]}, {"$inc": {"attempts": 1}}, return_document=ReturnDocument.AFTER)
        await record(ctx, "verify_identity", args, Decision("deny", "verify_failed", "ID and factor do not match"), "blocked",
                     {"verified": False, "attempts": ctx.identity["attempts"]})
        if ctx.identity["attempts"] >= config.MAX_IDENTITY_ATTEMPTS:
            ctx.escalations.append(Decision("escalate", "identity_failed", "3 failed identity checks"))
            return {"verified": False, "attempts_left": 0, "escalated": True}
        return {"verified": False, "attempts_left": config.MAX_IDENTITY_ATTEMPTS - ctx.identity["attempts"]}

    async def check_facts(self, ctx: Ctx, reply: Any) -> list[str]:
        return [f"fact {f.value!r} does not appear verbatim in reply_en" for f in reply.facts if f.value not in reply.reply_en]

    # ---------- context blocks 1 and 4 ----------

    def policy_block(self) -> str:
        return f"{self.pack.persona}\n{self.pack.policy_text.strip()}"

    async def in_scope(self, ctx: Ctx) -> str:
        scopes = self.scopes(ctx, {})
        out = {}
        for s in self.pack.context.get("in_scope", []):
            out[s.collection] = jsonable([d async for d in store.db[s.collection].find(
                self.scoped_filter(s.collection, s.filter, scopes), {f: 1 for f in s.fields} | {"_id": 0}).limit(s.limit)])
        return f"# 4. {self.pack.labels.get('primary', 'Records').title()} in scope\n" + json.dumps(out, default=str)

    # ---------- seed ----------

    async def reset(self) -> dict[str, int]:
        with open(f"{config.ROOT}/packs/{self.pack.name}/seed.json") as f:
            seed = json.load(f)
        for name in {*store.COLLECTIONS, *self.pack.collections, *seed}:
            await store.db[name].delete_many({})
        await store.ensure_indexes()
        counts = {}
        for name, docs in seed.items():
            for d in docs:
                d.setdefault("_id", ObjectId())
                d.setdefault("created_at", now())
            if docs:
                await store.db[name].insert_many(docs)
            counts[name] = len(docs)
        return counts
