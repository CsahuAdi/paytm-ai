"""SIMULATED Paytm channel for the kirana pack (hackathon demo, fake data, not affiliated with Paytm). Text only.
The selected customer is pre-verified like a matched WhatsApp number; real trust would come from the Paytm session."""
import random
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

from app import config, core
from app import db as store
from app import events
from app.db import jsonable, now

router = APIRouter()


class PaytmIn(BaseModel):
    phone: str
    text: str
    client_msg_id: str | None = None


@router.get("/paytm")
async def page() -> FileResponse:
    return FileResponse(f"{config.ROOT}/static/paytm.html")


@router.get("/api/paytm/customers")
async def customers() -> list[dict]:
    return [{"phone": c["phone"], "customer_id": c["customer_id"], "name": c.get("name_latin") or c["name"], "language": c.get("language")}
            async for c in store.db.customers.find({}).sort("customer_id", 1)]


@router.post("/api/paytm/chat")
async def chat(body: PaytmIn) -> dict:
    if await store.db.customers.find_one({"phone": body.phone}) is None:
        raise HTTPException(404, "unknown customer")
    inb = core.Inbound(channel="paytm", external_id=body.client_msg_id or uuid.uuid4().hex, sender=body.phone, text=body.text.strip()[:2000])
    return (await core.handle(inb)).__dict__


@router.get("/api/paytm/messages")
async def messages(phone: str) -> list[dict]:
    cust = await store.db.customers.find_one({"phone": phone})
    if cust is None:
        raise HTTPException(404, "unknown customer")
    ticket_ids = [t["_id"] async for t in store.db.tickets.find({"customer_id": cust["_id"]}, {"_id": 1})]
    rows = [m async for m in store.db.messages.find({"ticket_id": {"$in": ticket_ids}}).sort("created_at", 1)]
    return [{"id": str(m["_id"]), "author": m["author"], "author_name": m.get("author_name"), "text": m["text"], "at": jsonable(m["created_at"])}
            for m in rows]


@router.get("/api/paytm/transactions")
async def my_transactions(phone: str) -> list[dict]:
    cust = await store.db.customers.find_one({"phone": phone})
    if cust is None:
        raise HTTPException(404, "unknown customer")
    rows = [t async for t in store.db.transactions.find({"customer_id": cust["customer_id"]}).sort("date", -1).limit(8)]
    return [{"txn_id": t["txn_id"], "amount": t["amount"], "status": t["status"], "items": t.get("items", ""), "resolution": t.get("resolution", "none"),
             "date": jsonable(t["date"])[:10], "discount": t.get("discount", 0)} for t in rows]


@router.get("/api/paytm/coupons")
async def my_coupons(phone: str) -> list[dict]:
    cust = await store.db.customers.find_one({"phone": phone})
    if cust is None:
        raise HTTPException(404, "unknown customer")
    rows = [c async for c in store.db.coupons.find({"customer_id": cust["customer_id"]}).sort("created_at", -1).limit(10)]
    return [{"code": c["code"], "amount": c["amount"], "status": c["status"], "source": c["source"],
             "expires": jsonable(c["expires_at"])[:10]} for c in rows]


class PayIn(BaseModel):
    phone: str
    amount: int = Field(ge=1, le=100000)
    coupon_code: str | None = None


@router.post("/api/paytm/pay")
async def pay(body: PayIn) -> dict:
    """SIMULATED payment at the store. A valid coupon (yours, unexpired, unused) takes up to its value off and is marked
    redeemed (compare-and-set, so a double click redeems once). Creates a transaction so the loop shows up in the data."""
    cust = await store.db.customers.find_one({"phone": body.phone})
    if cust is None:
        raise HTTPException(404, "unknown customer")
    merchant = await store.db.merchants.find_one({}) or {}
    discount = 0
    if body.coupon_code:
        coupon = await store.db.coupons.find_one_and_update(
            {"code": body.coupon_code, "customer_id": cust["customer_id"], "status": "issued", "expires_at": {"$gt": now()}},
            {"$set": {"status": "redeemed", "redeemed_at": now()}}, return_document=ReturnDocument.AFTER)
        if coupon is None:
            raise HTTPException(409, "That coupon can't be used: it must be yours, not expired and not already used")
        discount = min(coupon["amount"], body.amount)
    for _ in range(50):
        txn_id = f"T-{random.randint(5000, 5999)}"
        if not await store.db.transactions.find_one({"txn_id": txn_id}):
            break
    await store.db.transactions.insert_one({
        "txn_id": txn_id, "customer_id": cust["customer_id"], "merchant_id": merchant.get("merchant_id"), "amount": body.amount - discount,
        "gross": body.amount, "discount": discount, "coupon_code": body.coupon_code, "status": "success",
        "items": "Payment at the store (simulated)", "date": now(), "resolution": "none", "source": "paytm_pay"})
    events.publish("payment", None, customer_id=cust["customer_id"], coupon=body.coupon_code)
    return {"txn_id": txn_id, "paid": body.amount - discount, "discount": discount, "coupon": body.coupon_code, "simulated": True}
