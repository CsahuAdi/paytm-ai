"""Pack schema. pack.yaml is validated here at startup; an invalid pack stops the server from starting."""
import os
import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,60}$")  # document keys and paths: never '$', never operators
OPS = {"==", "!=", "<", "<=", ">", ">=", "in", "not_in", "exists", "missing"}
ENGINE_TOOLS = {"verify_identity", "confirm_action", "escalate", "send_notification", "reply_to_customer"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def check_keys(value: Any, where: str) -> None:
    """Every mapping key in a template must be a plain field name, so no '$' operator can be authored."""
    if isinstance(value, dict):
        for k, v in value.items():
            if not KEY.match(str(k)):
                raise ValueError(f"{where}: key {k!r} is not a plain field name")
            check_keys(v, where)
    elif isinstance(value, list):
        for v in value:
            check_keys(v, where)


class Condition(Strict):
    """{field, op, value} or exactly one of {all: [...]}, {any: [...]}, {not: {...}}. Walked by our code; never eval()."""
    field: str | None = None
    op: str | None = None
    value: Any = None
    all: list["Condition"] | None = None
    any: list["Condition"] | None = None
    not_: "Condition | None" = Field(default=None, alias="not")

    @model_validator(mode="after")
    def shape(self) -> "Condition":
        groups = [g for g in (self.all, self.any, self.not_) if g is not None]
        leaf = self.field is not None
        if leaf == bool(groups) or len(groups) > 1:
            raise ValueError("a condition is either {field, op, value} or exactly one of all/any/not")
        if leaf and (self.op not in OPS or not KEY.match(self.field or "")):
            raise ValueError(f"bad condition: field={self.field!r} op={self.op!r}")
        return self


class DenyRule(Strict):
    rule_id: str
    reason: str
    when: Condition


class TierRule(Strict):
    when: Condition
    tier: Literal["allow", "confirm", "approve"]
    rule_id: str
    reason: str = ""


class Rule(Strict):
    deny_if: list[DenyRule] = []
    tiers: list[TierRule] = []  # first match wins; human approval lifts only these, never deny_if
    tier: Literal["allow", "confirm", "approve"] | None = None
    rule_id: str = "pack_rule"


class Param(Strict):
    type: Literal["string", "integer", "number", "boolean"] = "string"
    description: str = ""
    pattern: str | None = None
    max_length: int = 100
    minimum: float | None = None
    maximum: float | None = None
    enum: list[str] | None = None


class DbSpec(Strict):
    op: Literal["find", "search", "update"]  # search: MongoDB $text over the collection's text_index
    collection: str
    filter: dict[str, Any] = {}
    fields: list[str] = []
    limit: int = Field(default=10, le=50)
    as_: str = Field(default="target", alias="as")
    set: dict[str, Any] = {}
    inc: dict[str, int | float] = {}
    text: str | None = None  # search only: a "{params.x}" template; the engine builds the $text query itself

    @model_validator(mode="after")
    def keys(self) -> "DbSpec":
        if (self.op == "search") != bool(self.text):
            raise ValueError("db.text is required for op search and only allowed there")
        for part in ("filter", "set", "inc"):
            check_keys(getattr(self, part), f"db.{part}")
        if not NAME.match(self.as_):
            raise ValueError(f"bad alias {self.as_!r}")
        return self


class ToolDef(Strict):
    name: str
    description: str
    effect: Literal["read", "write", "irreversible"]
    kind: Literal["db"]  # db only; http/composite/plugin kinds are roadmap
    label: str | None = None  # dashboard name, e.g. "Loan renewal"
    public: bool = False  # read tools a visitor may use before verification
    params: dict[str, Param] = {}
    db: DbSpec
    rule: Rule | None = None
    idempotency: str | None = None
    result: dict[str, Any] = {}

    @field_validator("name")
    @classmethod
    def name_ok(cls, v: str) -> str:
        if not NAME.match(v):
            raise ValueError(f"bad tool name {v!r}")
        return v

    @model_validator(mode="after")
    def coherent(self) -> "ToolDef":
        for p in self.params:
            if not NAME.match(p):
                raise ValueError(f"{self.name}: bad param name {p!r}")
        if self.effect != "read":
            if self.db.op != "update":
                raise ValueError(f"{self.name}: a write tool must use op update")
            if not self.idempotency:
                raise ValueError(f"{self.name}: write tools need an idempotency key template")
        if self.public and self.effect != "read":
            raise ValueError(f"{self.name}: only read tools can be public")
        return self


class Verification(Strict):
    param: str
    param_description: str
    lookup: dict[str, str]  # {collection, field[, subject_ref]}
    factors: dict[Literal["text", "voice"], Literal["email", "phone_last4"]]
    factor_description: str


class Collection(Strict):
    owner_field: str | None = None  # personal entity: the engine scopes every query to the verified subject
    text_index: list[str] = []  # fields for search_* tools (MongoDB text index)


class InScope(Strict):
    collection: str
    filter: dict[str, Any] = {}
    fields: list[str]
    limit: int = Field(default=5, le=20)


class Pack(Strict):
    name: str
    product: str
    persona: str
    policy_file: str
    default_language: str = "en-IN"
    labels: dict[str, str] = {}
    verification: Verification
    collections: dict[str, Collection]
    facts: list[str]
    protected_patterns: list[str] = []  # IDs kept verbatim through translation (masked like order numbers)
    plugins: list[str] = []  # developer-written plugin tools this pack may be granted (see plugins/); loaded, not granted
    reviewers: list[str] = []  # extra named reviewers for this pack (e.g. the merchant); a label, not authentication
    channels: list[str] = ["web"]  # extra inbound channels this pack enables (e.g. paytm: a simulated chat page)
    welcome: str = ""  # first line shown on the chat pages (not stored as a message)
    suggestions: list[str] = []  # tappable example messages on the chat pages (they fill the input; nothing is sent)
    hints: list[str] = []  # short lines on the empty chat page, e.g. which invented demo accounts exist
    escalation_reasons: list[str]
    context: dict[str, list[InScope]] = {}
    tools: list[ToolDef]
    policy_text: str = ""

    @model_validator(mode="after")
    def references(self) -> "Pack":
        names = [t.name for t in self.tools]
        if len(set(names)) != len(names) or ENGINE_TOOLS & set(names):
            raise ValueError("tool names must be unique and must not shadow engine tools")
        for c, spec in self.collections.items():
            if not NAME.match(c):
                raise ValueError(f"bad collection name {c!r}")
            for f in [spec.owner_field or "", *spec.text_index]:
                if f and not KEY.match(f):
                    raise ValueError(f"{c}: bad field name {f!r}")
        for pattern in self.protected_patterns:
            if len(pattern) > 120:
                raise ValueError("protected pattern too long")
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"protected pattern {pattern!r}: {e}") from e
        for t in self.tools:
            if t.db.op == "search" and not self.collections[t.db.collection].text_index:
                raise ValueError(f"{t.name}: search needs collections.{t.db.collection}.text_index")
        for t in self.tools:
            if t.db.collection not in self.collections:
                raise ValueError(f"{t.name}: collection {t.db.collection} is not declared under collections")
        for s in self.context.get("in_scope", []):
            if s.collection not in self.collections:
                raise ValueError(f"in_scope collection {s.collection} is not declared")
            check_keys(s.filter, "context.in_scope.filter")
        return self


def load(name: str, root: str) -> Pack:
    if not NAME.match(name):
        raise ValueError(f"bad pack name {name!r}")
    folder = os.path.join(root, "packs", name)
    with open(os.path.join(folder, "pack.yaml")) as f:
        raw = yaml.safe_load(f)
    with open(os.path.join(folder, raw.get("policy_file", "policy.md"))) as f:
        raw["policy_text"] = f.read()
    return Pack.model_validate(raw)
