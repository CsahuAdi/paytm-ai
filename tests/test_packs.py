"""Config-only pack engine: schema validation, injection defences, scoping and data rules. No LLM, no database."""
import copy

import pytest
import yaml
from pydantic import ValidationError

from app import config
from app.packs import expr
from app.packs.runtime import Runtime, build_model
from app.packs.spec import Condition, Pack, Param, load

PACK = load("librarian", config.ROOT)
RT = Runtime(PACK)
RENEW, WAIVE = RT.tools["renew_loan"], RT.tools["waive_fine"]
RAW = yaml.safe_load(open(f"{config.ROOT}/packs/librarian/pack.yaml"))


def tool(raw: dict, name: str) -> dict:
    return next(t for t in raw["tools"] if t["name"] == name)


def session(subject: str = "M-1001") -> dict:
    return {"session": {"subject_id": subject, "verified": True}}


# --- the server refuses an invalid pack ---

@pytest.mark.parametrize("mutate", [
    lambda r: tool(r, "renew_loan")["db"]["filter"].update({"$where": "1"}),  # operator key in a filter
    lambda r: tool(r, "renew_loan")["db"]["set"].update({"$set": {"x": 1}}),  # operator key in an update
    lambda r: tool(r, "renew_loan")["rule"]["deny_if"][0]["when"].update({"op": "eval"}),  # unknown operator
    lambda r: tool(r, "renew_loan")["rule"]["deny_if"][0]["when"].update({"all": []}),  # leaf and group at once
    lambda r: tool(r, "renew_loan").update({"idempotency": None}),  # write tool without an idempotency key
    lambda r: tool(r, "renew_loan")["db"].update({"collection": "users"}),  # undeclared collection
    lambda r: tool(r, "get_member_loans").update({"name": "verify_identity"}),  # shadows an engine tool
    lambda r: tool(r, "renew_loan").update({"public": True}),  # public write tool
    lambda r: tool(r, "search_catalog")["db"].pop("text"),  # search without a query template
    lambda r: r["collections"]["books"].update({"text_index": []}),  # search without a text index
    lambda r: r["protected_patterns"].append("(unclosed"),  # invalid regex
])
def test_invalid_pack_is_rejected(mutate):
    raw = copy.deepcopy(RAW)
    mutate(raw)
    with pytest.raises(ValidationError):
        Pack.model_validate(raw)


# --- params are typed and cannot carry operators ---

@pytest.mark.parametrize("bad", ["$ne", " $gt", "{session.subject_id}", "L-50011", "M-1001", {"$ne": None}, ["L-5001"], 5001])
def test_param_injection_rejected(bad):
    with pytest.raises(ValidationError):
        RT.models["renew_loan"][1].model_validate({"loan_id": bad})


def test_param_extra_keys_rejected_and_valid_accepted():
    model = RT.models["renew_loan"][1]
    assert model.model_validate({"loan_id": "L-5001"}).loan_id == "L-5001"
    with pytest.raises(ValidationError):
        model.model_validate({"loan_id": "L-5001", "member_id": "M-1002"})


def test_generated_types():
    m = build_model("t", {"n": Param(type="integer", minimum=1, maximum=5), "c": Param(enum=["a", "b"])})
    assert m.model_validate({"n": 3, "c": "a"}).n == 3
    for bad in ({"n": 9, "c": "a"}, {"n": "3", "c": "a"}, {"n": 3, "c": "z"}):
        with pytest.raises(ValidationError):
            m.model_validate(bad)


# --- templates and scoping ---

def test_whole_value_template_keeps_type_and_scope_is_forced():
    scopes = {"params": {"loan_id": "L-5003"}} | session("M-1001")
    tampered = {"loan_id": "{params.loan_id}", "member_id": "M-1002"}  # a template trying to reach another member
    f = RT.scoped_filter("loans", tampered, scopes)
    assert f == {"loan_id": "L-5003", "member_id": "M-1001"}


def test_unverified_scope_matches_nothing():
    f = RT.scoped_filter("fines", {"fine_id": "F-7002"}, {"params": {}, "session": {"subject_id": None}})
    assert f["member_id"] is None


def test_days_after():
    assert expr.fill({"days_after": "loan.due_date", "days": 14}, {"loan": {"due_date": "2026-11-03"}}) == "2026-11-17"


# --- condition grammar ---

def test_condition_grammar():
    s = {"loan": {"renewals": 2, "reserved_by_other": False}}
    c = lambda d: Condition.model_validate(d)  # noqa: E731
    assert expr.holds(c({"field": "loan.renewals", "op": ">=", "value": 2}), s)
    assert not expr.holds(c({"field": "loan.missing", "op": ">", "value": 1}), s)
    assert expr.holds(c({"any": [{"field": "loan.reserved_by_other", "op": "==", "value": True},
                                 {"not": {"field": "loan.renewals", "op": "<", "value": 2}}]}), s)
    assert expr.holds(c({"field": "loan.missing", "op": "missing"}), s)


# --- data rules (deny is hard, approval lifts tiers only) ---

def loan(**kw) -> dict:
    return {"loan_id": "L-1", "due_date": "2026-11-03", "renewals": 0, "reserved_by_other": False} | kw


def test_renew_rules():
    assert RT.decide(RENEW, {"loan": loan()}).rule_id == "renew_confirm"
    assert RT.decide(RENEW, {"loan": loan(renewals=2)}).rule_id == "max_renewals"
    assert RT.decide(RENEW, {"loan": loan(reserved_by_other=True)}).rule_id == "reserved_by_other"
    assert RT.decide(RENEW, {"loan": loan(renewals=2)}, approved=True).result == "deny"
    assert RT.decide(RENEW, {"loan": None}).rule_id == "not_found"


def test_waive_tiers():
    fine = lambda amount, status="unpaid": {"fine": {"amount": amount, "status": status}}  # noqa: E731
    assert RT.decide(WAIVE, fine(60)).rule_id == "waive_confirm"
    assert RT.decide(WAIVE, fine(100)).rule_id == "waive_confirm"
    assert (RT.decide(WAIVE, fine(150)).result, RT.decide(WAIVE, fine(150)).rule_id) == ("approve", "fine_over_limit")
    assert RT.decide(WAIVE, fine(150), approved=True).rule_id == "approved"
    assert RT.decide(WAIVE, fine(150, "waived"), approved=True).rule_id == "no_unpaid_fine"


def test_write_tool_without_tier_is_denied():
    tool = RENEW.model_copy(update={"rule": RENEW.rule.model_copy(update={"tier": None, "tiers": []})})
    assert RT.decide(tool, {"loan": loan()}).rule_id == "no_tier_declared"


def test_search_query_cannot_carry_operators():
    model = RT.models["search_catalog"][1]
    assert model.model_validate({"query": "monsoon"}).query == "monsoon"
    for bad in ["$where", {"$regex": ".*"}, "x" * 61]:
        with pytest.raises(ValidationError):
            model.model_validate({"query": bad})


def test_pack_patterns_and_labels():
    assert PACK.protected_patterns and RT.tool_labels()["renew_loan"] == "Loan renewal"
    assert RT.tools["search_catalog"].public and RT.tools["search_catalog"].effect == "read"
