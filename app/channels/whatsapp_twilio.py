"""WhatsApp via Twilio sandbox: validate signature, ack at once, process in the background, reply via REST."""
import json
import logging
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from twilio.request_validator import RequestValidator

from app import config

log = logging.getLogger("whatsapp")
router = APIRouter()
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def public_url(request: Request) -> str:
    """Twilio signs the public tunnel URL, not the local one uvicorn sees."""
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{config.PUBLIC_BASE_URL}{request.url.path}{query}"


async def capture(form: dict) -> None:
    with open(f"{config.ROOT}/fixtures/captured_twilio_{int(time.time())}.json", "w") as f:
        json.dump(form, f, indent=1, ensure_ascii=False)


@router.post("/webhooks/twilio")
async def twilio_webhook(request: Request, background: BackgroundTasks) -> Response:
    form = dict(await request.form())
    signature = request.headers.get("X-Twilio-Signature", "")
    if not RequestValidator(config.TWILIO_AUTH_TOKEN).validate(public_url(request), form, signature):
        raise HTTPException(403, "bad signature")
    background.add_task(capture, form)
    return Response(EMPTY_TWIML, media_type="application/xml")
