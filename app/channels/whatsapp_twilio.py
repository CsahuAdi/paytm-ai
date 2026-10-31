"""WhatsApp via Twilio sandbox: validate signature, ack at once, process in the background, reply via REST."""
import asyncio
import logging
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from twilio.request_validator import RequestValidator
from twilio.rest import Client

from app import config, core
from app.lang import sarvam

log = logging.getLogger("whatsapp")
router = APIRouter()
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
SIMULATED_PREFIX = "+91980000"  # seed/fixture numbers: never message them for real
_client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN) if config.TWILIO_ACCOUNT_SID else None


def public_url(request: Request) -> str:
    """Twilio signs the public tunnel URL, not the local one uvicorn sees."""
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{config.PUBLIC_BASE_URL}{request.url.path}{query}"


async def send(to_phone: str, text: str) -> None:
    if to_phone.startswith(SIMULATED_PREFIX) or _client is None:
        log.info("simulated WhatsApp to %s: %s", to_phone, text[:80])
        return
    await asyncio.to_thread(_client.messages.create, from_=config.TWILIO_WHATSAPP_FROM, to=f"whatsapp:{to_phone}", body=text[:1500])


async def voice_note_text(form: dict[str, Any]) -> tuple[str | None, str | None]:
    """Download a WhatsApp voice note (HTTP basic auth with the account SID/token) and transcribe it."""
    url = form.get("MediaUrl0")
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as http:
        r = await http.get(url, auth=(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN))
        r.raise_for_status()
    text, language, _ = await sarvam.transcribe(r.content, "note.ogg")
    return text, language


async def process(form: dict[str, Any]) -> core.Outcome | None:
    phone = str(form.get("From", "")).removeprefix("whatsapp:")
    text, language, modality, note = str(form.get("Body") or "").strip(), None, "text", None
    if int(form.get("NumMedia") or 0) and str(form.get("MediaContentType0", "")).startswith("audio"):
        modality = "voice"
        try:
            text, language = await voice_note_text(form)
        except Exception as e:  # noqa: BLE001
            log.warning("voice note failed: %s", e)
            note = "Sorry, I couldn't listen to that voice note. Could you type your message instead?"
    if note or not text:
        await send(phone, note or "Please send your question as a text or voice message.")
        return None
    out = await core.handle(core.Inbound(channel="whatsapp", external_id=form.get("MessageSid"), sender=phone, text=text,
                                         modality=modality, language=language))
    if out.reply and not out.duplicate:
        try:
            await send(phone, out.reply)
        except Exception as e:  # noqa: BLE001
            log.error("WhatsApp reply failed: %s", e)
    return out


async def deliver(ticket: dict[str, Any], text: str, last: dict[str, Any]) -> None:
    await send(last["sender"], text)


core.SENDERS["whatsapp"] = deliver


async def _webhook(request: Request, background: BackgroundTasks, signed_urls: list[str]) -> Response:
    form = dict(await request.form())
    signature = request.headers.get("X-Twilio-Signature", "")
    validator = RequestValidator(config.TWILIO_AUTH_TOKEN)
    if not any(validator.validate(url, form, signature) for url in signed_urls):
        raise HTTPException(403, "bad signature")
    if config.DEBUG and request.query_params.get("sync"):  # simulator/tests: process inline
        out = await process(form)
        return Response(f'{{"ticket_id": "{out.ticket_id if out else ""}"}}', media_type="application/json")
    background.add_task(process, form)
    return Response(EMPTY_TWIML, media_type="application/xml")


@router.post("/webhooks/twilio")
async def twilio_webhook(request: Request, background: BackgroundTasks) -> Response:
    return await _webhook(request, background, [public_url(request)])


@router.post("/")
async def twilio_root(request: Request, background: BackgroundTasks) -> Response:
    """The sandbox URL is sometimes saved as the bare domain; Twilio then signs exactly that URL."""
    if "X-Twilio-Signature" not in request.headers:
        raise HTTPException(405, "Method Not Allowed")
    return await _webhook(request, background, [config.PUBLIC_BASE_URL, f"{config.PUBLIC_BASE_URL}/"])
