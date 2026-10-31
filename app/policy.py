"""Rules engine. Plain Python outside the model: the model proposes, this decides.

decide() reads only the proposed tool + arguments and facts loaded from the database (PolicyState).
It never sees model reasoning or customer text. Deny rules are hard: `approved=True` (a human clicked
Approve) lifts only `refund_limit` and `vip_review`, and the executor calls decide() again at run time.
"""
from dataclasses import dataclass, field
from typing import Any, Literal

from app.config import AUTO_REFUND_LIMIT_INR, MAX_IDENTITY_ATTEMPTS, REPEAT_CONTACT_LIMIT

Result = Literal["allow", "confirm", "approve", "escalate", "deny"]

READ_TOOLS = {"get_order", "get_tracking"}
ORDER_TOOLS = {"get_order", "get_tracking", "issue_refund", "create_reship", "cancel_order"}
DATA_TOOLS = ORDER_TOOLS | {"send_notification", "confirm_action"}
APPROVAL_LIFTS = {"refund_limit", "vip_review"}
ESCALATION_REASONS = {"legal_threat", "fraud_signal", "distress", "customer_requested_human"}


@dataclass(frozen=True)
class Decision:
    result: Result
    rule_id: str
    reason: str


@dataclass
class PolicyState:
    verified: bool = False
    tier: str = "standard"
    identity_attempts: int = 0
    repeat_contacts_30d: int = 0
    order: dict[str, Any] | None = None  # the order named in the args, if it belongs to this customer
    order_exists: bool = False
    prior_refunds: int = 0  # executed refunds on this order
    extra: dict[str, Any] = field(default_factory=dict)


def decide(tool: str, args: dict[str, Any], s: PolicyState, approved: bool = False) -> Decision:
    if tool == "reply_to_customer":
        return Decision("allow", "reply", "Replies always allowed")
    if tool == "escalate":
        return Decision("escalate", f"escalate_{args.get('reason', 'other')}", "Agent asked for a human")
    if tool == "verify_identity":
        if s.identity_attempts >= MAX_IDENTITY_ATTEMPTS:
            return Decision("escalate", "identity_failed", f"{MAX_IDENTITY_ATTEMPTS} failed identity checks")
        return Decision("allow", "verify", "Identity check allowed")

    # Deny rules (hard; approval cannot lift them)
    if tool in DATA_TOOLS and not s.verified:
        return Decision("deny", "unverified", "Identity not verified; data and action tools are blocked")
    if tool in ORDER_TOOLS and s.order is None:
        why = "Order belongs to another customer" if s.order_exists else "Order not found"
        return Decision("deny", "order_not_owned", why)
    if tool == "issue_refund":
        amount = args.get("amount", 0)
        total = s.order["total"] if s.order else 0
        if not isinstance(amount, int) or amount <= 0:
            return Decision("deny", "invalid_amount", "Refund amount must be a positive whole number of rupees")
        if s.prior_refunds > 0 or (s.order or {}).get("refund_total", 0) > 0:
            return Decision("deny", "second_refund", "This order already has a refund")
        if amount > total:
            return Decision("deny", "refund_exceeds_total", f"Refund Rs {amount} is above the order total Rs {total}")
    if tool == "cancel_order" and s.order and s.order.get("status") != "processing":
        return Decision("deny", "cancel_shipped", f"Order is {s.order.get('status')}; only unshipped orders can be cancelled")
    if tool == "create_reship" and s.order and s.order.get("status") not in ("shipped", "delivered"):
        return Decision("deny", "reship_not_shipped", "Only shipped or delivered orders can be reshipped")

    # Allow
    if tool in READ_TOOLS or tool in ("send_notification", "confirm_action"):
        return Decision("allow", "read" if tool in READ_TOOLS else tool, "Allowed")

    # Approve (a human must click Approve) / confirm (the customer must say yes)
    if tool == "issue_refund":
        amount = args["amount"]
        if s.tier == "vip" and not approved:
            return Decision("approve", "vip_review", "Every VIP refund needs human approval")
        if amount > AUTO_REFUND_LIMIT_INR and not approved:
            return Decision("approve", "refund_limit", f"Refund above Rs {AUTO_REFUND_LIMIT_INR} needs human approval")
        if approved:
            return Decision("allow", "approved", "Approved by a human")
        return Decision("confirm", "refund_confirm", "Customer must confirm the refund")
    if tool == "create_reship":
        return Decision("confirm", "reship_confirm", "Customer must confirm the replacement")
    if tool == "cancel_order":
        return Decision("confirm", "cancel_confirm", "Customer must confirm the cancellation")
    return Decision("deny", "unknown_tool", f"No rule allows {tool}")


def decide_reply(sentiment: str, confidence: str, escalation_reason: str, s: PolicyState) -> Decision | None:
    """Escalation triggers read at the end of a turn. Returns None when the turn needs no human."""
    if escalation_reason in ESCALATION_REASONS:
        return Decision("escalate", escalation_reason, f"Agent flagged {escalation_reason}")
    if sentiment == "distressed":
        return Decision("escalate", "distress", "Customer seems distressed")
    if confidence == "low":
        return Decision("escalate", "low_confidence", "Agent confidence is low")
    if s.verified and s.repeat_contacts_30d >= REPEAT_CONTACT_LIMIT:
        return Decision("escalate", "repeat_contact", f"Contact number {s.repeat_contacts_30d} in 30 days")
    return None
