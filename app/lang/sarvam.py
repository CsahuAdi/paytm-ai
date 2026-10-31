"""Sarvam API: STT, text-lid, translate, TTS. Every call is metered; non-demo calls stop at Rs 10 left."""
import base64
import logging
import re

from sarvamai import AsyncSarvamAI

from app import config
from app.lang import sarvam_meter

log = logging.getLogger("sarvam")
_client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY, timeout=15)


class SarvamUnavailable(Exception):
    """Budget stop, 402 or any API failure: callers fall back to English."""


def _guard() -> None:
    if not sarvam_meter.allowed(demo=config.DEMO_MODE):
        raise SarvamUnavailable(f"Sarvam budget stop (Rs {sarvam_meter.left():.2f} left)")


async def identify(text: str) -> tuple[str, str]:
    """Returns (language_code, script_code)."""
    _guard()
    try:
        r = await _client.text.identify_language(input=text[:1000])
    except Exception as e:  # noqa: BLE001
        raise SarvamUnavailable(f"text-lid: {e}") from e
    sarvam_meter.add("lid_char", len(text[:1000]))
    return r.language_code or "", r.script_code or ""


def chunks(text: str, limit: int = 900) -> list[str]:
    """Split at sentence boundaries to stay under mayura's 1,000-character limit."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for sentence in re.split(r"(?<=[.!?।])\s+", text):
        if cur and len(cur) + len(sentence) + 1 > limit:
            parts.append(cur)
            cur = ""
        cur = f"{cur} {sentence}".strip()
        while len(cur) > limit:
            parts.append(cur[:limit])
            cur = cur[limit:]
    if cur:
        parts.append(cur)
    return parts


async def translate(text: str, source: str, target: str, output_script: str | None = None) -> str:
    _guard()
    out = []
    for part in chunks(text):
        kw = {"output_script": output_script} if output_script else {}
        try:
            r = await _client.text.translate(input=part, source_language_code=source, target_language_code=target,
                                             model=config.SARVAM_TRANSLATE_MODEL, mode="formal", numerals_format="international", **kw)
        except Exception as e:  # noqa: BLE001
            raise SarvamUnavailable(f"translate: {e}") from e
        sarvam_meter.add("translate_char", len(part))
        out.append(r.translated_text)
    return " ".join(out)


async def transcribe(audio: bytes, filename: str, language: str | None = None, seconds: float = 10.0) -> tuple[str, str, float | None]:
    """Returns (transcript, language_code, language_probability)."""
    _guard()
    try:
        r = await _client.speech_to_text.transcribe(file=(filename, audio), model=config.SARVAM_STT_MODEL,
                                                    language_code=language or "unknown", mode="transcribe")
    except Exception as e:  # noqa: BLE001
        raise SarvamUnavailable(f"stt: {e}") from e
    sarvam_meter.add("stt_second", seconds)
    return r.transcript or "", r.language_code or "", r.language_probability


async def speak(text: str, language: str) -> bytes | None:
    """bulbul:v4-flash with one persona per language; None when the language has no v4-flash persona."""
    speaker = config.TTS_PERSONAS.get(language)
    if speaker is None:
        return None
    _guard()
    try:
        r = await _client.text_to_speech.convert(text=text, language_code=language, model=config.SARVAM_TTS_MODEL, speaker=speaker)
    except Exception as e:  # noqa: BLE001
        raise SarvamUnavailable(f"tts: {e}") from e
    sarvam_meter.add("tts_char", len(text))
    return base64.b64decode("".join(r.audios))
