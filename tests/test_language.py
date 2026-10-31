"""Language layer tests that spend no Sarvam credit (translation is mocked)."""
import asyncio

import pytest

from app.lang import facts_check, layer, sarvam

REPLY = "Your refund of Rs 1,499 for order RA-10234 is done. Tracking BD4471209IN, arriving 2 November."


def test_mask_and_restore_round_trip():
    values = facts_check.protected(REPLY, ["RA-10234"])
    assert {"RA-10234", "Rs 1,499", "BD4471209IN", "2 November"} <= set(values)
    masked, table = facts_check.mask(REPLY, values)
    assert "RA-10234" not in masked and "{F" in masked
    restored, missing = facts_check.unmask(masked, table)
    assert restored == REPLY and missing == []


def test_dropped_placeholder_is_reported():
    masked, table = facts_check.mask(REPLY, facts_check.protected(REPLY, []))
    broken = masked.replace("{F1}", "")
    _, missing = facts_check.unmask(broken, table)
    assert missing


def run(coro):
    return asyncio.run(coro)


def test_translation_that_drops_a_fact_falls_back_to_english(monkeypatch):
    async def lossy(text, source, target, output_script=None):
        return text.replace("{F1}", "1034")  # mayura really did this to RA-10234

    monkeypatch.setattr(sarvam, "translate", lossy)
    out = run(layer.from_english(REPLY, "hi-IN", "text", ["RA-10234"]))
    assert out.language == "en-IN" and out.text == REPLY and out.meta["translation_failed"]


def test_translation_keeps_facts(monkeypatch):
    async def faithful(text, source, target, output_script=None):
        return "अनुवाद: " + text

    monkeypatch.setattr(sarvam, "translate", faithful)
    out = run(layer.from_english(REPLY, "hi-IN", "text", ["RA-10234"]))
    assert out.language == "hi-IN" and "RA-10234" in out.text and "Rs 1,499" in out.text


def test_sarvam_failure_reads_original_and_flags(monkeypatch):
    async def down(text):
        raise sarvam.SarvamUnavailable("402 credits exhausted")

    monkeypatch.setattr(sarvam, "identify", down)
    out = run(layer.to_english("मेरा ऑर्डर कहाँ है?", None, "text"))
    assert out.language == "en-IN" and out.text_en == "मेरा ऑर्डर कहाँ है?" and out.meta["translation_failed"]


def test_unsupported_language_gets_english(monkeypatch):
    async def urdu(text):
        return "ur-IN", "Arab"

    monkeypatch.setattr(sarvam, "identify", urdu)
    out = run(layer.to_english("میرا آرڈر کہاں ہے؟", None, "text"))
    assert out.language == "en-IN" and out.meta["unsupported_language"] == "ur-IN"


@pytest.mark.parametrize("text,english", [
    ("Where is my order? It has been a week.", True),
    ("Yes, please go ahead with the refund.", True),
    ("RA-10234", True),
    ("mera order kahan hai bhai", False),
    ("मेरा ऑर्डर कहाँ है?", False),
])
def test_looks_english(text, english):
    assert layer.looks_english(text) is english


def test_english_never_reaches_sarvam(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("English text sent to Sarvam")

    monkeypatch.setattr(sarvam, "identify", boom)
    monkeypatch.setattr(sarvam, "translate", boom)
    assert run(layer.to_english("Where is my order RA-10234?", None, "text")).language == "en-IN"
    assert run(layer.from_english("Your order is on the way.", "en-IN", "text", [])).text == "Your order is on the way."


def test_chunks_respect_limit():
    long = "This is a sentence. " * 120
    parts = sarvam.chunks(long, 900)
    assert all(len(p) <= 900 for p in parts) and "".join(parts).replace(" ", "") == long.replace(" ", "")
