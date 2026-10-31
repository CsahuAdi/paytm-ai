"""book_slot: reserve a study room. Needs a plugin because it checks for overlapping bookings, generates a BK-#### ID,
writes `bookings` and an `outbox` email together. Loosest tier: confirm (the member says yes first)."""
import random
import re
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from app import db as store
from app.db import now
from app.policy import Decision
from plugins import plugin_tool

IST = ZoneInfo("Asia/Kolkata")
OPEN_HOUR, CLOSE_HOUR = 9, 20
FMT = "%Y-%m-%dT%H:%M"


def _iso_start(v: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?", v):
        raise ValueError("start must look like 2026-11-02T16:00 (India time)")
    return datetime.fromisoformat(v).strftime(FMT)


class BookSlotParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_id: str = Field(pattern=r"^SR-[A-Z]$", description="Study room ID, e.g. SR-B")
    start: Annotated[str, AfterValidator(_iso_start)] = Field(description="Start time in India time, e.g. 2026-11-02T16:00")
    duration_min: Literal[30, 60, 90, 120] = Field(description="Length in minutes: 30, 60, 90 or 120")


def window(start: str, minutes: int) -> tuple[str, str]:
    begin = datetime.strptime(start, FMT)
    return begin.strftime(FMT), (begin + timedelta(minutes=minutes)).strftime(FMT)


def overlaps(a: tuple[str, str], b: tuple[str, str]) -> bool:
    """Half-open intervals [start, end): back-to-back bookings do not clash. ISO strings compare in time order."""
    return a[0] < b[1] and b[0] < a[1]


async def _clash(room_id: str, slot: tuple[str, str]) -> dict[str, Any] | None:
    async for b in store.db.bookings.find({"room_id": room_id, "status": "booked"}):
        if overlaps(slot, (b["start"], b["end"])):
            return b
    return None


@plugin_tool(name="book_slot", label="Study-room booking", params=BookSlotParams, effect="write", loosest_tier="confirm",
             description="Book a study room for the member: room_id, start time (India time) and 30/60/90/120 minutes. "
                         "The member must say yes first.",
             idempotency=lambda p: f"book:{p['resource_id']}:{p['start']}:{p['duration_min']}")
class BookSlot:
    async def check(self, params: dict[str, Any], member: dict[str, Any] | None) -> Decision:
        room = await store.db.study_rooms.find_one({"room_id": params["resource_id"]})
        if room is None:
            return Decision("deny", "no_such_room", f"There is no study room {params['resource_id']}")
        slot = window(params["start"], params["duration_min"])
        begin = datetime.strptime(slot[0], FMT).replace(tzinfo=IST)
        if begin < datetime.now(IST):
            return Decision("deny", "in_the_past", "That time has already passed")
        end = begin + timedelta(minutes=params["duration_min"])
        if begin.hour < OPEN_HOUR or end > begin.replace(hour=CLOSE_HOUR, minute=0):
            return Decision("deny", "outside_hours", f"Study rooms are open {OPEN_HOUR}:00-{CLOSE_HOUR}:00")
        if await _clash(params["resource_id"], slot):
            return Decision("deny", "slot_taken", f"{room['name']} is already booked at that time")
        return Decision("confirm", "booking_confirm", "The member must confirm the booking")

    async def run(self, action: dict[str, Any], member: dict[str, Any]) -> dict[str, Any]:
        await store.db.bookings.create_index("booking_id", unique=True)  # idempotent; cheap after the first call
        p = action["args"]
        slot = window(p["start"], p["duration_min"])
        room = await store.db.study_rooms.find_one({"room_id": p["resource_id"]})
        for _ in range(20):
            booking_id = f"BK-{random.randint(1000, 9999)}"
            try:
                await store.db.bookings.insert_one({"booking_id": booking_id, "room_id": p["resource_id"], "member_id": member["member_id"],
                                                    "start": slot[0], "end": slot[1], "status": "booked", "action_id": action["_id"],
                                                    "created_at": now()})
                break
            except DuplicateKeyError:
                continue
        body = (f"Hello {member.get('name_latin') or member['name']},\n\nYour booking {booking_id} is confirmed: {room['name']} on "
                f"{slot[0].replace('T', ' at ')} until {slot[1][11:]}.\n\nRiverside Public Library")
        status = await _send(member["email"], f"Booking {booking_id} confirmed", body)
        await store.db.outbox.insert_one({"to": member["email"], "subject": f"Booking {booking_id} confirmed", "body": body,
                                          "booking_id": booking_id, "status": status, "created_at": now()})
        return {"booking_id": booking_id, "room": room["name"], "start": slot[0], "end": slot[1],
                "email": "sent" if status == "sent" else "confirmation queued in the outbox", "systems": ["bookings", "notifications"]}


async def _send(to: str, subject: str, body: str) -> str:
    """Real AgentMail only for a real address; fake (example.com) members and any failure go to the outbox."""
    from app import config
    if to.endswith("example.com") or not (config.AGENTMAIL_API_KEY and config.AGENTMAIL_INBOX_ID):
        return "outbox"
    try:
        from agentmail import AsyncAgentMail
        await AsyncAgentMail(api_key=config.AGENTMAIL_API_KEY).inboxes.messages.send(config.AGENTMAIL_INBOX_ID, to=to, subject=subject, text=body)
        return "sent"
    except Exception:  # noqa: BLE001
        return "outbox"
