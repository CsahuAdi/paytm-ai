"""Container start: seed the demo data once, when the database has no tickets yet (a fresh hosted cluster). Safe on every boot.

Contact details are the invented seed ones (no DEMO_CUSTOMER_* mapping), so a hosted instance never carries real contacts.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db as store  # noqa: E402
from app.seed_data import reset  # noqa: E402


async def main() -> None:
    await store.connect()
    if await store.db.tickets.count_documents({}, limit=1):
        print(store.db.name, "already has data; not seeding", flush=True)
    else:
        print(store.db.name, "empty; seeding", json.dumps(await reset(demo_contacts=False)), flush=True)
    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
