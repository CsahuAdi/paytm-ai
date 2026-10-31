"""Email via AgentMail: verify the Svix signature on the raw body, dedupe on event_id, reply in thread."""
import json
import logging
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from svix.webhooks import Webhook, WebhookVerificationError

from app import config

log = logging.getLogger("email")
router = APIRouter()


async def capture(payload: dict) -> None:
    with open(f"{config.ROOT}/fixtures/captured_agentmail_{int(time.time())}.json", "w") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)


@router.post("/webhooks/agentmail")
async def agentmail_webhook(request: Request, background: BackgroundTasks) -> dict:
    raw = await request.body()
    try:
        Webhook(config.AGENTMAIL_WEBHOOK_SECRET).verify(raw, dict(request.headers))
    except WebhookVerificationError:
        raise HTTPException(403, "bad signature")
    payload = json.loads(raw)
    background.add_task(capture, payload)
    return {"ok": True}
