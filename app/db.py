"""MongoDB: one AsyncMongoClient for the process, collections, indexes and small helpers."""
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app import config

_client: AsyncMongoClient | None = None
db: AsyncDatabase = None  # type: ignore[assignment]

COLLECTIONS = ["customers", "orders", "refunds", "identities", "tickets", "messages", "actions"]


def now() -> datetime:
    return datetime.now(timezone.utc)


def oid(value: str | ObjectId) -> ObjectId:
    return value if isinstance(value, ObjectId) else ObjectId(value)


async def connect() -> AsyncDatabase:
    global _client, db
    _client = AsyncMongoClient(config.MONGO_URI, tz_aware=True)
    db = _client[config.MONGO_DB]
    await ensure_indexes()
    return db


async def close() -> None:
    if _client is not None:
        await _client.close()


async def ensure_indexes() -> None:
    has_string = lambda field: {field: {"$type": "string"}}  # noqa: E731
    await db.messages.create_index([("channel", ASCENDING), ("external_id", ASCENDING)], unique=True,
                                   partialFilterExpression=has_string("external_id"))
    await db.messages.create_index([("ticket_id", ASCENDING), ("created_at", ASCENDING)])
    await db.actions.create_index("idempotency_key", unique=True, partialFilterExpression=has_string("idempotency_key"))
    await db.actions.create_index([("ticket_id", ASCENDING), ("created_at", ASCENDING)])
    await db.identities.create_index([("channel", ASCENDING), ("key", ASCENDING)], unique=True)
    await db.tickets.create_index([("customer_id", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)])
    await db.orders.create_index("order_number", unique=True)
    await db.customers.create_index("email", unique=True)
    await db.refunds.create_index("idempotency_key", unique=True)


def jsonable(doc: Any) -> Any:
    """ObjectIds and datetimes to strings, recursively, for API responses and LLM tool results."""
    if isinstance(doc, dict):
        return {("id" if k == "_id" else k): jsonable(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [jsonable(x) for x in doc]
    if isinstance(doc, ObjectId):
        return str(doc)
    if isinstance(doc, datetime):
        return doc.isoformat(timespec="seconds")
    return doc
