"""Web chat adapter: POST a message, get the reply; async messages arrive over SSE scoped to the session."""
import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from app import core

router = APIRouter()


class ChatIn(BaseModel):
    session_id: str
    text: str
    language: str = "auto"  # auto | en-IN | hi-IN | ...
    client_msg_id: str | None = None


@router.post("/api/chat")
async def chat(body: ChatIn) -> dict:
    inb = core.Inbound(channel="web", external_id=body.client_msg_id or uuid.uuid4().hex, sender=body.session_id,
                       text=body.text.strip()[:4000], language=None if body.language == "auto" else body.language)
    out = await core.handle(inb)
    return out.__dict__
