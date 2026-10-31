"""Fake seed data sized to the locked policy values. reset() wipes all 7 collections and reloads it.

Demo beats: Priya (Hindi, voice + damaged item + cross-channel), Arjun (angry VIP), Rohan (Rs 3,500
approval and Rs 50,000 over-total deny), Kavya (second language, unshipped order to cancel).
Meera has two recent tickets so her next contact is the third in 30 days.
"""
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
    return {"customers": customers, "orders": orders, "refunds": refunds, "tickets": tickets}


async def reset(demo_contacts: bool = True) -> dict[str, int]:
    for name in COLLECTIONS:
        await store.db[name].delete_many({})
    await store.ensure_indexes()
    counts = {}
    for name, docs in build(demo_contacts).items():
        if docs:
            await store.db[name].insert_many(docs)
        counts[name] = len(docs)
    return counts
