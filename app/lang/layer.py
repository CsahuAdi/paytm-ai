"""Language edges. English inside; Sarvam converts at the boundary. English text never goes to Sarvam.

Inbound: pinned language, or a local English check, or Sarvam text-lid. Romanized Indic text (Latin script, not
English) is translated with source=auto and answered in romanized form. Any failure: the agent reads the original
text, the reply goes out in English, and the message is flagged translation_failed.
"""
import logging
import re
from dataclasses import dataclass, field

from app import config
from app.lang import facts_check, sarvam

log = logging.getLogger("lang")
ENGLISH_HINTS = set("""a an and are as at be but by can do for from have hello hi how i i'm if in is it me my no not of on or
order please refund my the this to was what when where why will with yes you your thanks thank ok okay sorry still
delivered damaged broken cancel email number tracking arrive help want need got received item has been week
day days today yesterday there here that they their just also very much any update"""
.split())
SUPPORTED_LINE = ("I can also help in Hindi, Bengali, Gujarati, Kannada, Malayalam, Marathi, Punjabi, Odia, Tamil and Telugu.")


@dataclass
class InText:
    language: str
    text_en: str
    meta: dict = field(default_factory=dict)


@dataclass
class OutText:
    text: str
    language: str
    meta: dict = field(default_factory=dict)


def looks_english(text: str) -> bool:
    if not text.isascii():
        return False
    words = re.findall(r"[a-zA-Z']{3,}", text.lower())
    if not words:
        return True
    return sum(w in ENGLISH_HINTS for w in words) / len(words) >= 0.25


async def to_english(text: str, pinned: str | None, modality: str) -> InText:
    if pinned == "en-IN" or (pinned is None and looks_english(text)):
        return InText("en-IN", text)
    try:
        if pinned:
            language, script = pinned, "Latn" if text.isascii() else "native"
        else:
            language, script = await sarvam.identify(text)
        if language == "en-IN":
            return InText("en-IN", text)
        if language not in config.LANGUAGES:
            return InText("en-IN", text, {"unsupported_language": language})
        roman = script == "Latn" or text.isascii()
        text_en = await sarvam.translate(text, "auto" if roman else language, "en-IN")
        return InText(language, text_en, {"script": "roman" if roman else "native", "detected": language})
    except sarvam.SarvamUnavailable as e:
        log.warning("inbound translation failed: %s", e)
        return InText("en-IN", text, {"translation_failed": True, "error": str(e)[:120], "wanted": pinned})


async def from_english(text_en: str, language: str, modality: str, facts: list[str], script: str = "native") -> OutText:
    if language == "en-IN" or language not in config.LANGUAGES:
        return OutText(text_en, "en-IN")
    values = facts_check.protected(text_en, facts)
    masked, table = facts_check.mask(text_en, values)
    output_script = "roman" if script == "roman" else ("spoken-form-in-native" if modality == "voice" else "fully-native")
    try:
        translated = await sarvam.translate(masked, "en-IN", language, output_script)
    except sarvam.SarvamUnavailable as e:
        log.warning("outbound translation failed: %s", e)
        return OutText(text_en, "en-IN", {"translation_failed": True, "error": str(e)[:120]})
    restored, missing = facts_check.unmask(translated, table)
    if missing:
        return OutText(text_en, "en-IN", {"facts_fallback": missing, "translation_failed": True})
    return OutText(restored, language, {"masked_facts": len(table)} if table else {})
