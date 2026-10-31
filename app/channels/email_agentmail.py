"""Email via AgentMail: verify the Svix signature on the raw body, dedupe on event_id, reply in thread."""
import json
import logging
import re
from email.utils import parseaddr
from typing import Any

from agentmail import AsyncAgentMail
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from svix.webhooks import Webhook, WebhookVerificationError

from app import config, core

log = logging.getLogger("email")
router = APIRouter()
_client = AsyncAgentMail(api_key=config.AGENTMAIL_API_KEY) if config.AGENTMAIL_API_KEY else None
SIMULATED_DOMAINS = ("example.com",)  # fixtures: never send real mail to these


def clean_body(message: dict[str, Any]) -> str:
    """AgentMail's extracted_text drops the quoted thread; also cut signatures and any leftover 'On ... wrote:'."""
    text = message.get("extracted_text") or message.get("text") or ""
    text = re.split(r"\n--\s*\n|\nSent via AgentMail", text)[0]
    text = re.split(r"\nOn .{5,200} wrote:", text, flags=re.S)[0]
    return text.strip()[:4000]


async def send_reply(message_id: str, to: str, text: str) -> None:
    if to.endswith(SIMULATED_DOMAINS) or _client is None:
        log.info("simulated email reply to %s: %s", to, text[:80])
        return
    await _client.inboxes.messages.reply(config.AGENTMAIL_INBOX_ID, message_id, text=text)


async def process(payload: dict[str, Any]) -> core.Outcome | None:
    msg = payload.get("message") or {}
    sender = parseaddr(msg.get("from_") or msg.get("from") or "")[1].lower()
    if not sender or sender == config.AGENTMAIL_INBOX_ID.lower() or "sent" in (msg.get("labels") or []):
        return None  # our own outbound mail
    body = clean_body(msg)
    if not body:
        return None
    inb = core.Inbound(channel="email", external_id=payload.get("event_id") or msg.get("message_id"), sender=sender, text=body,
                       thread_hint=msg.get("message_id"), meta={"email_thread_id": msg.get("thread_id"), "subject": msg.get("subject")})
    out = await core.handle(inb)
    if out.reply and not out.duplicate:
        try:
            await send_reply(msg["message_id"], sender, out.reply)
        except Exception as e:  # noqa: BLE001
            log.error("email reply failed: %s", e)
    return out


async def deliver(ticket: dict[str, Any], text: str, last: dict[str, Any]) -> None:
    """Async message (approval outcome, human reply) into the customer's latest email thread."""
    await send_reply(last["meta"].get("thread_hint") or "", last["sender"], text)


core.SENDERS["email"] = deliver


@router.post("/webhooks/agentmail")
async def agentmail_webhook(request: Request, background: BackgroundTasks) -> dict:
    raw = await request.body()
    try:
        Webhook(config.AGENTMAIL_WEBHOOK_SECRET).verify(raw, dict(request.headers))
    except WebhookVerificationError:
        raise HTTPException(403, "bad signature")
    payload = json.loads(raw)
    if payload.get("event_type") != "message.received":
        return {"ok": True, "ignored": payload.get("event_type")}
    if config.DEBUG and request.query_params.get("sync"):  # simulator/tests: process inline and return the outcome
        out = await process(payload)
        return {"ok": True, **(out.__dict__ if out else {})}
    background.add_task(process, payload)
    return {"ok": True}
