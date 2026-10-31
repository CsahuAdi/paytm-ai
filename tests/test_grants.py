"""Tool grants and the book_slot plugin: tier rules, tightening, catalogs, input validation. No LLM, no database."""
import asyncio

import pytest
from pydantic import ValidationError

from app import config, grants, registry
from app.packs.runtime import Runtime
from app.packs.spec import load
from app.policy import Decision
from plugins.book_slot import BookSlotParams, overlaps, window

LIB = Runtime(load("librarian", config.ROOT))


def with_pack(monkeypatch, runtime=LIB):
    monkeypatch.setattr(registry, "RUNTIME", runtime)


def with_grants(monkeypatch, table: dict):
    async def fake() -> dict:
        return table
    monkeypatch.setattr(grants, "current", fake)


def test_tier_order():
    assert grants.tighter("approve", "confirm") == "approve"
    assert grants.tighter("allow", "confirm") == "confirm"


def test_customer_service_catalog_keeps_current_tiers():
    cat = grants.catalog()  # PACK unset in tests: customer service
    assert {n: m.loosest_tier for n, m in cat.items()} == {n: t for n, (_, t) in grants.CS_TOOLS.items()}
    assert not grants.CORE & set(cat)  # verify/confirm/escalate/reply are never grantable


def test_librarian_catalog(monkeypatch):
    with_pack(monkeypatch)
    cat = grants.catalog()
    assert cat["book_slot"].source == "plugin" and cat["book_slot"].loosest_tier == "confirm"
    assert cat["waive_fine"].loosest_tier == "confirm" and cat["search_catalog"].loosest_tier == "allow"


@pytest.mark.parametrize("tool,tier,error", [("waive_fine", "allow", "looser"), ("book_slot", "allow", "looser"),
                                              ("waive_fine", "approve", None), ("nope", None, "catalog"), ("book_slot", "bogus", "looser")])
def test_admin_cannot_loosen(monkeypatch, tool, tier, error):
    with_pack(monkeypatch)
    msg = grants.validate_change(tool, tier, None)
    assert (msg is None) if error is None else (error in msg)


def test_cap_must_be_positive(monkeypatch):
    with_pack(monkeypatch)
    assert "at least 1" in grants.validate_change("book_slot", None, 0)


def test_adjust_tightens_never_loosens(monkeypatch):
    with_grants(monkeypatch, {"renew_loan": {"enabled": True, "tier": "approve"}, "waive_fine": {"enabled": True, "tier": "confirm"}})
    run = lambda tool, d: asyncio.run(grants.adjust(tool, d))  # noqa: E731
    assert run("renew_loan", Decision("confirm", "renew_confirm", "")).result == "approve"
    assert run("waive_fine", Decision("approve", "fine_over_limit", "")).result == "approve"  # rule stricter than grant stays
    assert run("renew_loan", Decision("deny", "max_renewals", "")).result == "deny"  # deny untouched
    assert run("renew_loan", Decision("escalate", "x", "")).result == "escalate"


def test_gate_refuses_ungranted(monkeypatch):
    with_pack(monkeypatch)
    with_grants(monkeypatch, {"renew_loan": {"enabled": True, "tier": "confirm"}, "book_slot": {"enabled": False, "tier": "confirm"}})
    assert asyncio.run(grants.gate("book_slot")).rule_id == "tool_not_granted"
    assert asyncio.run(grants.gate("waive_fine")).rule_id == "tool_not_granted"  # no grant row at all
    assert asyncio.run(grants.gate("renew_loan")) is None
    assert asyncio.run(grants.gate("verify_identity")) is None  # core tools are never gated


def test_book_slot_params():
    ok = BookSlotParams.model_validate({"resource_id": "SR-B", "start": "2026-11-02T16:00", "duration_min": 60})
    assert ok.start == "2026-11-02T16:00"
    for bad in [{"resource_id": "SR-1", "start": "2026-11-02T16:00", "duration_min": 60},
                {"resource_id": "SR-B", "start": "Monday 4pm", "duration_min": 60},
                {"resource_id": "SR-B", "start": "2026-11-02T16:00", "duration_min": 45},
                {"resource_id": "SR-B", "start": "2026-11-02T16:00", "duration_min": 60, "member_id": "M-1002"},
                {"resource_id": {"$ne": None}, "start": "2026-11-02T16:00", "duration_min": 60}]:
        with pytest.raises(ValidationError):
            BookSlotParams.model_validate(bad)


def test_overlap_math():
    a = window("2026-11-02T16:00", 60)
    assert overlaps(a, window("2026-11-02T16:30", 60))
    assert overlaps(a, window("2026-11-02T15:30", 120))
    assert not overlaps(a, window("2026-11-02T17:00", 30))  # back-to-back is fine
    assert not overlaps(a, window("2026-11-02T15:00", 60))
