"""Web voice: push-to-talk audio -> Sarvam STT -> core -> reply -> Sarvam TTS. Failures fall back to text on screen."""
import base64
import logging
import uuid

from fastapi import APIRouter, File, Form, UploadFile

from app import config, core
from app.lang import sarvam

log = logging.getLogger("voice")
router = APIRouter()
MAX_SECONDS = 30


@router.post("/api/voice")
async def voice(session_id: str = Form(...), language: str = Form("auto"), seconds: float = Form(10.0),
                text: str = Form(""), audio: UploadFile | None = File(None)) -> dict:
    """Send `audio` (browser webm) or, as the typed fallback, `text`."""
    pinned = None if language == "auto" else language
    notes = []
    transcript, detected, probability = text.strip(), pinned, None
    if audio is not None and not transcript:
        data = await audio.read()
        if config.DEBUG:  # keep the last browser recording to re-test STT offline
            with open(f"{config.ROOT}/data/last_voice.webm", "wb") as f:
                f.write(data)
        try:
            transcript, detected, probability = await sarvam.transcribe(data, audio.filename or "voice.webm", pinned,
                                                                        seconds=min(seconds, MAX_SECONDS))
        except sarvam.SarvamUnavailable as e:
            log.warning("STT failed: %s", e)
            return {"transcript": None, "reply": None, "audio_b64": None,
                    "note": "Speech recognition is unavailable right now. Please type your message below."}
        if not transcript.strip():
            return {"transcript": "", "reply": None, "audio_b64": None, "note": "I couldn't hear anything clear. Please try again."}
    if detected and detected not in config.LANGUAGES:
        detected = None  # unsupported: the layer replies in English
    out = await core.handle(core.Inbound(channel="voice", external_id=uuid.uuid4().hex, sender=session_id, text=transcript,
                                         modality="voice", language=detected or None,
                                         meta={"stt_language": detected, "stt_probability": probability}))
    audio_b64 = None
    if out.reply:
        try:
            speech = await sarvam.speak(out.reply, out.language)
            if speech is None:
                notes.append(f"No voice available for {config.LANGUAGES.get(out.language, out.language)}; showing text.")
            else:
                audio_b64 = base64.b64encode(speech).decode()
        except sarvam.SarvamUnavailable as e:
            log.warning("TTS failed: %s", e)
            notes.append("Voice reply unavailable right now; showing text.")
    if out.meta.get("translation_failed"):
        notes.append("Translation unavailable; replied in English.")
    return {"transcript": transcript, "language": detected, "reply": out.reply, "reply_en": out.reply_en, "reply_language": out.language,
            "audio_b64": audio_b64, "status": out.status, "lane": out.meta.get("lane"), "latency_s": out.meta.get("latency_s"),
            "note": " ".join(notes) or None, "mode": out.meta.get("mode")}
