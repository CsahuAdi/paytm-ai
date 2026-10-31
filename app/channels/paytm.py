"""SIMULATED Paytm channel for the kirana pack (hackathon demo, fake data, not affiliated with Paytm). Text only.
The selected customer is pre-verified like a matched WhatsApp number; real trust would come from the Paytm session."""
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import config, core
from app import db as store
from app.db import jsonable

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
