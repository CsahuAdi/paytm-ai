"""Post signed Twilio-shaped and AgentMail-shaped webhooks to the server, so tests spend no real messages.

Usage: python scripts/simulate_channels.py email --sender priya.sharma@example.com --text "Any update on RA-10234?"
       python scripts/simulate_channels.py whatsapp --sender +919800000101 --text "Any update?"
"""
import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone

import httpx
from svix.webhooks import Webhook
from twilio.request_validator import RequestValidator

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config  # noqa: E402


def email_payload(sender: str, text: str, subject: str = "Order question", event_id: str | None = None,
                  thread_id: str | None = None) -> dict:
    with open(f"{config.ROOT}/fixtures/agentmail_received.json") as f:
        payload = json.load(f)
    mid = f"<{uuid.uuid4().hex}@sim.example.com>"
    payload["event_id"] = event_id or uuid.uuid4().hex
    payload["message"].update({"from": sender, "from_": sender, "to": [config.AGENTMAIL_INBOX_ID], "subject": subject,
                               "message_id": mid, "thread_id": thread_id or uuid.uuid4().hex, "labels": ["received", "unread"],
                               "text": text, "extracted_text": text, "html": None, "preview": text[:80]})
    return payload


def post_fixture(base: str, kind: str, sender: str, text: str, event_id: str | None = None, **extra) -> tuple[int, dict]:
    if kind == "email":
        body = json.dumps(email_payload(sender, text, event_id=event_id, **extra))
        msg_id = f"msg_{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc)
        sig = Webhook(config.AGENTMAIL_WEBHOOK_SECRET).sign(msg_id, now, body)
        headers = {"svix-id": msg_id, "svix-timestamp": str(int(now.timestamp())), "svix-signature": sig, "content-type": "application/json"}
        r = httpx.post(f"{base}/webhooks/agentmail?sync=1", content=body, headers=headers, timeout=120)
    elif kind == "whatsapp":
        params = {"From": f"whatsapp:{sender}", "To": config.TWILIO_WHATSAPP_FROM, "Body": text, "NumMedia": "0",
                  "MessageSid": event_id or f"SM{uuid.uuid4().hex}", "ProfileName": "Simulated", **extra}
        path = "/webhooks/twilio?sync=1"
        sig = RequestValidator(config.TWILIO_AUTH_TOKEN).compute_signature(f"{config.PUBLIC_BASE_URL}{path}", params)
        r = httpx.post(f"{base}{path}", data=params, headers={"X-Twilio-Signature": sig}, timeout=120)
    else:
        raise ValueError(kind)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("kind", choices=["email", "whatsapp"])
    p.add_argument("--sender", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--event-id")
    p.add_argument("--base", default="http://localhost:8000")
    a = p.parse_args()
    code, body = post_fixture(a.base, a.kind, a.sender, a.text, a.event_id)
    print(code, json.dumps({k: body.get(k) for k in ("ticket_id", "reply", "status", "duplicate")}, ensure_ascii=False))
