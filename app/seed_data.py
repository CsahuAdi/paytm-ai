"""Fake seed data sized to the locked policy values. reset() wipes all 7 collections and reloads it.

Demo beats: Priya (Hindi, voice + damaged item + cross-channel), Arjun (angry VIP), Rohan (Rs 3,500
approval and Rs 50,000 over-total deny), Kavya (second language, unshipped order to cancel).
Meera has two recent tickets so her next contact is the third in 30 days.
"""
import itertools
from datetime import timedelta

from bson import ObjectId

from app import config
from app import db as store
from app.db import COLLECTIONS, now


def _shipment(carrier: str, tracking: str, status: str, eta_days: int, events: list[tuple[int, str, str]]) -> dict:
    t = now()
    return {
        "carrier": carrier, "tracking_no": tracking, "status": status,
        "eta": (t + timedelta(days=eta_days)).date().isoformat(),
        "events": [{"at": t - timedelta(days=d), "status": s, "location": loc} for d, s, loc in events],
    }


def _order(number: str, customer_id: ObjectId, status: str, items: list[tuple[str, str, int]], placed_days_ago: int,
           shipment: dict | None, refund_total: int = 0) -> dict:
    lines = [{"sku": sku, "name": name, "qty": 1, "price": price} for sku, name, price in items]
    return {
        "_id": ObjectId(), "order_number": number, "customer_id": customer_id, "status": status, "items": lines,
        "total": sum(x["price"] for x in lines), "placed_at": now() - timedelta(days=placed_days_ago),
        "shipment": shipment, "refund_total": refund_total,
    }


def build(demo_contacts: bool = True) -> dict[str, list[dict]]:
    priya, rohan, arjun, kavya, meera = (ObjectId() for _ in range(5))
    customers = [
        {"_id": priya, "name": "Priya Sharma", "email": (demo_contacts and config.DEMO_CUSTOMER_EMAIL) or "priya.sharma@example.com",
         "phone": (demo_contacts and config.DEMO_CUSTOMER_PHONE) or "+919800000101", "language": "hi-IN", "tier": "standard"},
        {"_id": rohan, "name": "Rohan Verma", "email": "rohan.verma@example.com", "phone": "+919800000102",
         "language": "en-IN", "tier": "standard"},
        {"_id": arjun, "name": "Arjun Mehta", "email": "arjun.mehta@example.com", "phone": "+919800000103",
         "language": "en-IN", "tier": "vip"},
        {"_id": kavya, "name": "Kavya Iyer", "email": "kavya.iyer@example.com", "phone": "+919800000104",
         "language": "ta-IN", "tier": "standard"},
        {"_id": meera, "name": "Meera Nair", "email": "meera.nair@example.com", "phone": "+919800000105",
         "language": "en-IN", "tier": "standard"},
    ]
    for c in customers:
        c["created_at"] = now() - timedelta(days=120)

    in_transit = [(5, "picked_up", "Mumbai"), (3, "in_transit", "Pune hub"), (1, "out_for_delivery", "Bengaluru")]
    orders = [
        _order("RA-10234", priya, "delivered", [("BLN-01", "Steel Blender 600W", 1499)], 7,
               _shipment("BlueDart", "BD4471209IN", "delivered", -1, [*in_transit, (0, "delivered", "Bengaluru")])),
        _order("RA-10198", priya, "delivered", [("KRT-07", "Cotton Kurta", 899)], 40,
               _shipment("Delhivery", "DV8812034IN", "delivered", -35, [(38, "delivered", "Bengaluru")]), refund_total=200),
        _order("RA-10311", rohan, "delivered", [("HP-220", "Noise-cancelling Headphones", 3500)], 10,
               _shipment("BlueDart", "BD4471388IN", "delivered", -4, [(4, "delivered", "Hyderabad")])),
        _order("RA-10322", rohan, "delivered", [("SW-901", "Fitness Smartwatch", 4199)], 12,
               _shipment("Ekart", "EK5520917IN", "delivered", -6, [(6, "delivered", "Hyderabad")])),
        _order("RA-10402", arjun, "shipped", [("ESP-3", "Espresso Machine", 12999)], 9,
               _shipment("Delhivery", "DV8812777IN", "delayed", 3, [(8, "picked_up", "Delhi"), (6, "in_transit", "Nagpur hub"),
                                                                     (2, "delayed", "Nagpur hub")])),
        _order("RA-10455", kavya, "processing", [("YGM-2", "Yoga Mat", 1199)], 1, None),
        _order("RA-10456", kavya, "shipped", [("BTL-5", "Steel Water Bottle", 499)], 3,
               _shipment("Ekart", "EK5520999IN", "in_transit", 2, [(2, "picked_up", "Chennai"), (1, "in_transit", "Chennai hub")])),
        _order("RA-10501", meera, "delivered", [("LMP-4", "Desk Lamp", 1299)], 20,
               _shipment("BlueDart", "BD4471500IN", "delivered", -15, [(15, "delivered", "Kochi")])),
    ]
    order_id = {o["order_number"]: o["_id"] for o in orders}
    refunds = [{"_id": ObjectId(), "order_id": order_id["RA-10198"], "ticket_id": None, "amount": 200,
                "reason": "Late delivery goodwill credit", "idempotency_key": "seed:refund:RA-10198", "created_at": now() - timedelta(days=34)}]

    def past_ticket(cust: ObjectId, days_ago: int, resolution: str) -> dict:
        t = now() - timedelta(days=days_ago)
        return {"_id": ObjectId(), "customer_id": cust, "identity_id": None, "status": "resolved", "mode": "ai",
                "language": "en-IN", "channels": ["web"], "order_ids": [], "summary": "", "summary_upto": None,
                "summary_version": 0, "resolution": resolution, "human_note": None, "escalation": None,
                "created_at": t, "updated_at": t}

    tickets = [
        past_ticket(arjun, 45, "Delivery of RA-10380 was late; apologised and shared tracking."),
        past_ticket(meera, 12, "Asked about RA-10501 delivery date; shared tracking."),
        past_ticket(meera, 5, "Desk lamp RA-10501 flickers; sent troubleshooting steps."),
    ]
    data = {"customers": customers, "orders": orders, "refunds": refunds, "tickets": tickets, "messages": [], "actions": []}
    # One earlier conversation for Rohan that ends in a blocked rule, so the desk's "Blocked by rules" tile is not zero before the demo.
    t, m, a = _history(rohan, "web", 8 * 1440, "resolved", [
        ("customer", "Order RA-10322, email rohan.verma@example.com. My smartwatch stopped working. Please refund Rs 9,000 for the trouble."),
        ("ai", "I'm sorry about the watch, Rohan. I can't refund more than the order total of Rs 4,199, so that request was blocked by our rules. I can offer a refund up to the order total or a replacement; which would you like?"),
        ("customer", "Let me think about it."),
        ("ai", "Of course. I'm here whenever you decide.")],
        [_act("verify_identity", {"order_number": "RA-10322", "factor": "***"}, "allow", "verify", "Identity check allowed", "executed", result={"verified": True}),
         _act("issue_refund", {"order_number": "RA-10322", "amount": 9000, "reason": "Watch stopped working"}, "deny", "refund_exceeds_total",
              "Refund Rs 9000 is above the order total Rs 4199", "blocked", result={"rule_id": "refund_exceeds_total"})],
        resolution="Customer paused the request.")  # neutral on purpose: past resolutions feed the model's memory
    data["tickets"].append(t)
    data["messages"].extend(m)
    data["actions"].extend(a)
    for name, docs in _scenario_data().items():
        data[name].extend(docs)
    return data


# ---------- scenario and judge customers (see docs/SCENARIOS.md) ----------

def _customer(cid: ObjectId, name: str, email: str, phone: str, language: str, tier: str = "standard") -> dict:
    return {"_id": cid, "name": name, "email": email, "phone": phone, "language": language, "tier": tier,
            "created_at": now() - timedelta(days=90)}


def _history(cust: ObjectId, channel: str, minutes_ago: float, status: str, turns: list[tuple[str, str]],
             actions: list[dict], resolution: str | None = None, language: str = "en-IN") -> tuple[dict, list[dict], list[dict]]:
    """A seeded ticket with its conversation and actions, marked seed=True so tests can tell them apart."""
    start = now() - timedelta(minutes=minutes_ago)
    tid = ObjectId()
    ticket = {"_id": tid, "customer_id": cust, "identity_id": None, "status": status, "mode": "ai", "language": language,
              "script": "native", "channels": [channel], "order_ids": [], "summary": "", "summary_upto": None, "summary_version": 0,
              "resolution": resolution, "human_note": None, "escalation": None, "model_calls": 0, "created_at": start,
              "updated_at": start + timedelta(minutes=len(turns))}
    msgs = [{"_id": ObjectId(), "ticket_id": tid, "author": author, "author_name": None, "channel": channel, "modality": "text",
             "language": "en-IN", "text": text, "text_en": text, "external_id": None, "sender": f"seed-{cust}",
             "meta": {"seed": True}, "seed": True, "created_at": start + timedelta(minutes=i)} for i, (author, text) in enumerate(turns)]
    acts = []
    for i, a in enumerate(actions):
        acts.append({"_id": ObjectId(), "ticket_id": tid, "customer_id": cust, "channel": channel, "args": {}, "result": None,
                     "idempotency_key": None, "decided_by": None, "decided_at": None, "decision_note": None, "seed": True,
                     "created_at": start + timedelta(minutes=i + 0.5), "updated_at": start + timedelta(minutes=i + 0.5), **a})
    return ticket, msgs, acts


def _act(tool: str, args: dict, outcome: str, rule: str, reason: str, status: str, **extra) -> dict:
    return {"tool": tool, "args": args, "tier": outcome, "decision": {"result": outcome, "rule_id": rule, "reason": reason},
            "status": status, **extra}


def _scenario_data() -> dict[str, list[dict]]:
    ananya, karthik, sneha, farhan, ishita, rohit, lakshmi, aarav, diya = (ObjectId() for _ in range(9))
    customers = [
        _customer(ananya, "Ananya Gupta", "ananya.gupta@example.com", "+919800000201", "en-IN"),
        _customer(karthik, "Karthik Reddy", "karthik.reddy@example.com", "+919800000202", "te-IN"),
        _customer(sneha, "Sneha Patil", "sneha.patil@example.com", "+919800000203", "mr-IN"),
        _customer(farhan, "Farhan Qureshi", "farhan.qureshi@example.com", "+919800000204", "en-IN", "vip"),
        _customer(ishita, "Ishita Banerjee", "ishita.banerjee@example.com", "+919800000205", "bn-IN"),
        _customer(rohit, "Rohit Malhotra", "rohit.malhotra@example.com", "+919800000206", "en-IN"),
        _customer(lakshmi, "Lakshmi Menon", "lakshmi.menon@example.com", "+919800000207", "ml-IN"),
        _customer(aarav, "Aarav Shah", "aarav.shah@example.com", "+919800000301", "en-IN"),
        _customer(diya, "Diya Kapoor", "diya.kapoor@example.com", "+919800000302", "hi-IN"),
    ]
    serial = itertools.count(4472101, 17)
    delivered = lambda city, d: _shipment("BlueDart", f"BD{next(serial)}IN", "delivered", -d,  # noqa: E731
                                          [(d + 3, "picked_up", "Mumbai"), (d + 1, "in_transit", f"{city} hub"), (d, "delivered", city)])
    orders = [
        _order("RA-20011", ananya, "shipped", [("EB-12", "Wireless Earbuds", 1799)], 3,
               _shipment("Delhivery", "DV8813011IN", "out_for_delivery", 0, [(3, "picked_up", "Delhi"), (1, "in_transit", "Gurugram hub"),
                                                                               (0, "out_for_delivery", "Noida")])),
        _order("RA-20012", ananya, "delivered", [("PC-03", "Phone Case", 499)], 9, delivered("Noida", 6)),
        _order("RA-20021", karthik, "shipped", [("LED-6", "Smart LED Bulb (pack of 4)", 1299)], 12,
               _shipment("Ekart", "EK5521021IN", "lost", 5, [(11, "picked_up", "Hyderabad"), (9, "in_transit", "Vijayawada hub"),
                                                              (6, "lost", "Vijayawada hub")])),
        _order("RA-20031", sneha, "delivered", [("PCK-5", "Pressure Cooker 5L", 2499)], 6, delivered("Pune", 2)),
        _order("RA-19876", sneha, "delivered", [("MXR-2", "Mixer Grinder", 3100)], 60, delivered("Pune", 55), refund_total=3100),
        _order("RA-20041", farhan, "processing", [("AP-900", "Air Purifier", 18999)], 1, None),
        _order("RA-20042", farhan, "delivered", [("CF-1", "Coffee Beans 1kg", 899)], 9, delivered("Lucknow", 1)),
        _order("RA-20051", ishita, "delivered", [("SAR-1", "Silk Saree", 3200), ("BLS-2", "Blouse Piece", 650)], 7, delivered("Kolkata", 3)),
        _order("RA-20052", ishita, "delivered", [("DUP-4", "Cotton Dupatta", 1100)], 25, delivered("Kolkata", 20), refund_total=1100),
        _order("RA-20061", rohit, "delivered", [("GC-5", "Gaming Console", 39999)], 5,
               _shipment("BlueDart", "BD4472610IN", "delivered", -2, [(4, "picked_up", "Mumbai"), (3, "in_transit", "Thane hub"),
                                                                     (2, "delivered (signed by R. Malhotra)", "Mumbai")])),
        _order("RA-20071", lakshmi, "shipped", [("AYO-3", "Ayurvedic Oil Set", 750)], 10,
               _shipment("Delhivery", "DV8813071IN", "delayed", 2, [(9, "picked_up", "Kochi"), (6, "delayed", "Kochi hub")])),
        _order("RA-30001", aarav, "delivered", [("BTS-7", "Bluetooth Speaker", 2999)], 6, delivered("Ahmedabad", 2)),
        _order("RA-30002", aarav, "shipped", [("DSK-2", "Desk Organizer", 599)], 2,
               _shipment("Ekart", "EK5530002IN", "in_transit", 2, [(1, "picked_up", "Surat"), (0, "in_transit", "Ahmedabad hub")])),
        _order("RA-30011", diya, "delivered", [("KUR-9", "Kurti Set", 1450)], 5, delivered("Jaipur", 1)),
        _order("RA-30012", diya, "processing", [("BED-3", "Double Bedsheet", 1899)], 0, None),
    ]
    oid = {o["order_number"]: o["_id"] for o in orders}
    refunds = [
        {"_id": ObjectId(), "order_id": oid["RA-19876"], "ticket_id": None, "amount": 3100, "reason": "Motor burnt out",
         "idempotency_key": "seed:refund:RA-19876", "created_at": now() - timedelta(days=50)},
        {"_id": ObjectId(), "order_id": oid["RA-20052"], "ticket_id": None, "amount": 1100, "reason": "Colour faded after first wash",
         "idempotency_key": "seed:refund:RA-20052", "created_at": now() - timedelta(days=10)},
    ]
    verify = lambda order: _act("verify_identity", {"order_number": order, "factor": "***"}, "allow", "verify",  # noqa: E731
                                "Identity check allowed", "executed", result={"verified": True})
    tickets, messages, actions = [], [], []

    def add(t: tuple[dict, list[dict], list[dict]]) -> dict:
        tickets.append(t[0])
        messages.extend(t[1])
        actions.extend(t[2])
        return t[0]

    add(_history(ananya, "web", 6 * 1440, "resolved", [
        ("customer", "Hi, where is my phone case? Order RA-20012, email ananya.gupta@example.com"),
        ("ai", "Thanks Ananya, you're verified. Your order RA-20012 is out for delivery and should reach you today.")],
        [verify("RA-20012"), _act("get_tracking", {"order_number": "RA-20012"}, "allow", "read", "Allowed", "executed",
                                  result={"order_number": "RA-20012"})],
        resolution="Shared tracking for RA-20012; delivered the same day."))
    sneha_t = add(_history(sneha, "email", 50 * 1440, "resolved", [
        ("customer", "My mixer grinder RA-19876 stopped working, the motor burnt out. I want a full refund of Rs 3,100."),
        ("ai", "I'm sorry about that. A refund of Rs 3,100 is above our automatic limit, so a teammate is reviewing it."),
        ("ai", "Good news: a teammate approved your refund of Rs 3,100 for order RA-19876. It has been processed.")],
        [verify("RA-19876"),
         _act("issue_refund", {"order_number": "RA-19876", "amount": 3100, "reason": "Motor burnt out"}, "approve", "refund_limit",
              "Refund above Rs 2000 needs human approval", "executed", idempotency_key="seed:act:refund:RA-19876",
              decided_by="Neha", decided_at=now() - timedelta(days=50) + timedelta(minutes=40),
              result={"refunded": 3100, "order_number": "RA-19876", "systems": ["payments", "orders", "notifications"]})],
        resolution="Refunded Rs 3,100 for RA-19876 after approval by Neha."))
    refunds[0]["ticket_id"] = sneha_t["_id"]
    ishita_t = add(_history(ishita, "whatsapp", 10 * 1440, "resolved", [
        ("customer", "The dupatta from RA-20052 faded after one wash. Can I get my Rs 1,100 back?"),
        ("ai", "I'm sorry about that. I can refund Rs 1,100 for order RA-20052. Shall I go ahead?"),
        ("customer", "Yes please"),
        ("ai", "Done: Rs 1,100 has been refunded for order RA-20052.")],
        [verify("RA-20052"),
         _act("issue_refund", {"order_number": "RA-20052", "amount": 1100, "reason": "Colour faded"}, "confirm", "refund_confirm",
              "Customer must confirm the refund", "executed", idempotency_key="seed:act:refund:RA-20052", decided_by="customer",
              decided_at=now() - timedelta(days=10) + timedelta(minutes=2),
              result={"refunded": 1100, "order_number": "RA-20052", "systems": ["payments", "orders", "notifications"]})],
        resolution="Refunded Rs 1,100 for RA-20052 (colour faded)."))
    refunds[1]["ticket_id"] = ishita_t["_id"]
    farhan_t = add(_history(farhan, "web", 25, "pending_approval", [
        ("customer", "Order RA-20042, email farhan.qureshi@example.com. My coffee beans arrived 4 days late. "
                     "I'd like a Rs 899 refund as goodwill."),
        ("ai", "I'm sorry for the delay, Farhan. As a VIP customer your refund request of Rs 899 goes to a teammate for review; "
               "you'll hear back shortly.")],
        [verify("RA-20042")]))
    actions.append({"_id": ObjectId(), "ticket_id": farhan_t["_id"], "customer_id": farhan, "channel": "web", "tool": "issue_refund",
                    "args": {"order_number": "RA-20042", "amount": 899, "reason": "Delivered 4 days late (goodwill)"},
                    "tier": "approve", "decision": {"result": "approve", "rule_id": "vip_review", "reason": "Every VIP refund needs human approval"},
                    "status": "pending_approval", "result": None, "idempotency_key": f"refund:{farhan_t['_id']}:RA-20042:899",
                    "decided_by": None, "decided_at": None, "decision_note": None, "seed": True,
                    "created_at": now() - timedelta(minutes=24), "updated_at": now() - timedelta(minutes=24)})
    farhan_t["order_ids"] = [oid["RA-20042"]]
    return {"customers": customers, "orders": orders, "refunds": refunds, "tickets": tickets, "messages": messages, "actions": actions}


async def reset(demo_contacts: bool = True) -> dict[str, int]:
    if config.PACK:  # a domain pack brings its own seed.json and database
        from app import registry
        return await registry.RUNTIME.reset()
    for name in COLLECTIONS:
        await store.db[name].delete_many({})
    await store.ensure_indexes()
    counts = {}
    for name, docs in build(demo_contacts).items():
        if docs:
            await store.db[name].insert_many(docs)
        counts[name] = len(docs)
    return counts
