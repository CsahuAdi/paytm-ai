"""Restore the demo seed data in one command: python scripts/reset_demo.py [--pack librarian]"""
import asyncio
import json
import os
import sys

if "--pack" in sys.argv:  # must be set before app.config is imported; the pack gets its own database
    os.environ["PACK"] = sys.argv[sys.argv.index("--pack") + 1]

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db as store  # noqa: E402
from app.seed_data import reset  # noqa: E402


async def main() -> None:
    await store.connect()
    print(store.db.name, json.dumps(await reset()))
    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
