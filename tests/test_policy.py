"""Unit tests for the rules engine: every rule, the boundaries, and approval never lifting a deny."""
import pytest

from app.policy import PolicyState, decide, decide_reply

ORDER = {"order_number": "RA-1", "total": 3500, "status": "delivered", "refund_total": 0}


def state(**kw) -> PolicyState:
    base = dict(verified=True, tier="standard", order=dict(ORDER), order_exists=True)
    base.update(kw)
    return PolicyState(**base)


def refund(amount: int, s: PolicyState, approved: bool = False):
    return decide("issue_refund", {"order_number": "RA-1", "amount": amount, "reason": "damaged"}, s, approved=approved)


# --- deny: identity ---

@pytest.mark.parametrize("tool", ["get_order", "get_tracking", "issue_refund", "create_reship", "cancel_order",
                                  "send_notification", "confirm_action"])
def test_data_tools_denied_before_verification(tool):
    d = decide(tool, {"order_number": "RA-1", "amount": 100, "reason": "x"}, state(verified=False))
    assert (d.result, d.rule_id) == ("deny", "unverified")


def test_unverified_denied_even_when_approved():
    assert refund(100, state(verified=False), approved=True).rule_id == "unverified"


def test_verify_allowed_and_escalates_after_three_failures():
    assert decide("verify_identity", {}, state(verified=False, identity_attempts=2)).result == "allow"
    d = decide("verify_identity", {}, state(verified=False, identity_attempts=3))
    assert (d.result, d.rule_id) == ("escalate", "identity_failed")


@pytest.mark.parametrize("exists,why", [(False, "not found"), (True, "another customer")])
def test_order_not_owned(exists, why):
    d = decide("get_order", {"order_number": "RA-9"}, state(order=None, order_exists=exists))
    assert (d.result, d.rule_id) == ("deny", "order_not_owned")
    assert why in d.reason


# --- deny: refunds ---

def test_refund_above_order_total_denied():
    d = refund(3501, state())
    assert (d.result, d.rule_id) == ("deny", "refund_exceeds_total")


def test_refund_50000_denied_even_with_approval():
    assert refund(50000, state(), approved=True).rule_id == "refund_exceeds_total"


def test_refund_equal_to_total_is_not_denied():
    assert refund(3500, state()).result != "deny"


def test_second_refund_denied_by_refund_rows():
    assert refund(100, state(prior_refunds=1)).rule_id == "second_refund"


def test_second_refund_denied_by_order_refund_total():
    s = state()
    s.order["refund_total"] = 200
    assert refund(100, s).rule_id == "second_refund"
    assert refund(100, s, approved=True).rule_id == "second_refund"


@pytest.mark.parametrize("amount", [0, -5])
def test_invalid_amount(amount):
    assert refund(amount, state()).rule_id == "invalid_amount"


# --- confirm / approve tiers ---

def test_refund_at_limit_needs_customer_confirmation():
    d = refund(2000, state())
    assert (d.result, d.rule_id) == ("confirm", "refund_confirm")


def test_refund_one_rupee_over_limit_needs_approval():
    d = refund(2001, state())
    assert (d.result, d.rule_id) == ("approve", "refund_limit")


def test_vip_refund_always_needs_approval():
    d = refund(100, state(tier="vip"))
    assert (d.result, d.rule_id) == ("approve", "vip_review")


@pytest.mark.parametrize("amount,tier", [(3500, "standard"), (100, "vip"), (3000, "vip")])
def test_approval_lifts_only_limit_and_vip(amount, tier):
    d = refund(amount, state(tier=tier), approved=True)
    assert (d.result, d.rule_id) == ("allow", "approved")


def test_reship_and_cancel_need_confirmation():
    assert decide("create_reship", {"order_number": "RA-1"}, state()).result == "confirm"
    s = state()
    s.order["status"] = "processing"
    assert decide("cancel_order", {"order_number": "RA-1"}, s).result == "confirm"


def test_cancel_shipped_order_denied():
    for status in ("shipped", "delivered", "cancelled"):
        s = state()
        s.order["status"] = status
        assert decide("cancel_order", {"order_number": "RA-1"}, s).rule_id == "cancel_shipped"


def test_reship_unshipped_order_denied():
    s = state()
    s.order["status"] = "processing"
    assert decide("create_reship", {"order_number": "RA-1"}, s).rule_id == "reship_not_shipped"


# --- allow ---

@pytest.mark.parametrize("tool", ["get_order", "get_tracking"])
def test_reads_allowed_after_verification(tool):
    assert decide(tool, {"order_number": "RA-1"}, state()).result == "allow"


def test_notification_and_confirm_allowed_after_verification():
    assert decide("send_notification", {"channel": "email", "message": "hi"}, state(order=None)).result == "allow"
    assert decide("confirm_action", {"action_id": "x"}, state(order=None)).result == "allow"


def test_unknown_tool_denied():
    assert decide("delete_database", {}, state()).result == "deny"


# --- escalation triggers at the end of a turn ---

@pytest.mark.parametrize("reason", ["legal_threat", "fraud_signal", "distress", "customer_requested_human"])
def test_escalation_reasons(reason):
    d = decide_reply("calm", "high", reason, state())
    assert (d.result, d.rule_id) == ("escalate", reason)


def test_low_confidence_and_distress_escalate():
    assert decide_reply("calm", "low", "none", state()).rule_id == "low_confidence"
    assert decide_reply("distressed", "high", "none", state()).rule_id == "distress"


def test_third_contact_in_30_days_escalates():
    assert decide_reply("calm", "high", "none", state(repeat_contacts_30d=2)) is None
    assert decide_reply("calm", "high", "none", state(repeat_contacts_30d=3)).rule_id == "repeat_contact"


def test_calm_turn_needs_no_human():
    assert decide_reply("angry", "medium", "none", state()) is None
