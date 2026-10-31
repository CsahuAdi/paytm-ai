"""Smoke test: Twilio credentials + sandbox, AgentMail send / receive / reply-in-thread.

Usage: python scripts/smoke_channels.py [twilio|agentmail ...]
Spends no WhatsApp messages; the AgentMail test mails the inbox itself.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config  # noqa: E402


def twilio() -> None:
    from twilio.rest import Client
    c = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    acct = c.api.v2010.accounts(config.TWILIO_ACCOUNT_SID).fetch()
    print(f"TWILIO account status={acct.status} type={acct.type}")
    recent = c.messages.list(limit=5)
    print(f"TWILIO recent messages: {[(m.direction, m.from_, m.status) for m in recent]}")


def agentmail() -> None:
    from agentmail import AgentMail
    c = AgentMail(api_key=config.AGENTMAIL_API_KEY)
    inbox = c.inboxes.get(config.AGENTMAIL_INBOX_ID)
    print(f"AGENTMAIL inbox={inbox.inbox_id} display={getattr(inbox, 'display_name', None)}")
    subject = f"smoke {int(time.time())}"
    sent = c.inboxes.messages.send(config.AGENTMAIL_INBOX_ID, to=config.AGENTMAIL_INBOX_ID, subject=subject,
                                   text="Order RA-10234 smoke test. Reply in thread please.")
    print(f"AGENTMAIL sent message_id={sent.message_id} thread_id={sent.thread_id}")
    received = None
    for _ in range(15):
        time.sleep(2)
        items = c.inboxes.messages.list(config.AGENTMAIL_INBOX_ID, limit=10).messages
        received = next((m for m in items if m.subject == subject and m.message_id != sent.message_id), None)
        if received:
            break
    if not received:
        print("AGENTMAIL receive: not seen within 30 s (self-send may be deduped into one message)")
        items = c.inboxes.messages.list(config.AGENTMAIL_INBOX_ID, limit=5).messages
        print("  latest:", [(m.subject, m.message_id, getattr(m, "labels", None)) for m in items])
        received = next((m for m in items if m.subject == subject), None)
    if received:
        full = c.inboxes.messages.get(config.AGENTMAIL_INBOX_ID, received.message_id)
        print(f"AGENTMAIL got id={full.message_id} thread={full.thread_id} from={full.from_} in_reply_to={getattr(full, 'in_reply_to', None)}")
        print(f"  fields: {sorted(k for k, v in full.model_dump().items() if v is not None)}")
        reply = c.inboxes.messages.reply(config.AGENTMAIL_INBOX_ID, received.message_id, text="Thread reply smoke test.")
        print(f"AGENTMAIL reply message_id={reply.message_id} thread_id={reply.thread_id} same_thread={reply.thread_id == full.thread_id}")
    hooks = c.webhooks.list()
    print(f"AGENTMAIL webhooks: {[(h.url, h.event_types) for h in hooks.webhooks]}")


if __name__ == "__main__":
    for name in sys.argv[1:] or ["twilio", "agentmail"]:
        try:
            globals()[name]()
        except Exception as e:  # noqa: BLE001
            print(f"{name.upper()} FAIL {type(e).__name__}: {str(e)[:400]}")
