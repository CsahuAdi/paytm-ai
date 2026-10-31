"""Smoke test: Sarvam TTS -> STT auto-detect, text-lid, mayura round trip, bulbul voices.

Writes audio to data/smoke/ for listening and adds the estimated spend to data/sarvam_spend.json.
"""
import base64
import json
import os
import time

from dotenv import load_dotenv
from sarvamai import SarvamAI

load_dotenv()
OUT = "data/smoke"
os.makedirs(OUT, exist_ok=True)
client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])
spend = 0.0  # Rs: translate 2/1k chars, lid 3.5/10k chars, stt 0.49/min, tts v4-flash 1/1k chars


def tts(text: str, lang: str, speaker: str, name: str) -> str:
    global spend
    t = time.perf_counter()
    r = client.text_to_speech.convert(text=text, language_code=lang, model="bulbul:v4-flash", speaker=speaker)
    spend += len(text) / 1000 * 1.0
    path = f"{OUT}/{name}.wav"
    with open(path, "wb") as f:
        f.write(base64.b64decode("".join(r.audios)))
    print(f"TTS {name} speaker={speaker} {round(time.perf_counter() - t, 2)}s -> {path}")
    return path


def stt(path: str) -> None:
    global spend
    t = time.perf_counter()
    with open(path, "rb") as f:
        r = client.speech_to_text.transcribe(file=f, model="saaras:v4", language_code="unknown", mode="transcribe")
    spend += 10 / 60 * 0.49
    print(f"STT {path}: lang={r.language_code} p={r.language_probability} {round(time.perf_counter() - t, 2)}s text={r.transcript!r}")


def lid(text: str) -> None:
    global spend
    r = client.text.identify_language(input=text)
    spend += len(text) / 10000 * 3.5
    print(f"LID {text!r}: lang={r.language_code} script={r.script_code}")


def translate(text: str, src: str, tgt: str, script: str | None = "fully-native") -> str:
    global spend
    t = time.perf_counter()
    kw = {"output_script": script} if script else {}
    r = client.text.translate(input=text, source_language_code=src, target_language_code=tgt, model="mayura:v1",
                              mode="formal", numerals_format="international", **kw)
    spend += len(text) / 1000 * 2.0
    print(f"MT {src}->{tgt} [{script}] {round(time.perf_counter() - t, 2)}s: {r.translated_text!r}")
    return r.translated_text


EN = "Your refund of Rs 1,499 for order RA-10234 has been approved. It will reach your account by 5 November."

print("--- text-lid")
for s in ["मेरा ऑर्डर कहाँ है? ऑर्डर नंबर RA-10234", "Where is my order? It has been a week.",
          "mera order kahan hai? order number RA-10234", "bhai mera parcel abhi tak nahi aaya"]:
    lid(s)

print("--- mayura round trip (facts must survive)")
hi_native = translate(EN, "en-IN", "hi-IN", "fully-native")
hi_spoken = translate(EN, "en-IN", "hi-IN", "spoken-form-in-native")
back = translate(hi_native, "hi-IN", "en-IN", None)
for fact in ["RA-10234", "1,499"]:
    print(f"fact {fact}: native={fact in hi_native} spoken={fact in hi_spoken} back={fact in back}")

print("--- bulbul v4-flash voices")
tts(hi_native, "hi-IN", "suchitra_hi_ecomm", "hi_native_suchitra")
tts(hi_spoken, "hi-IN", "suchitra_hi_ecomm", "hi_spoken_suchitra")
tts(hi_native, "hi-IN", "ritu_hi_customer", "hi_native_ritu")
en_path = tts("Hi, where is my order RA-10234? It has been a week.", "en-IN", "simran_en_customer", "en_simran")
hi_q = tts("नमस्ते, मेरा ऑर्डर RA-10234 अभी तक नहीं आया, कहाँ है?", "hi-IN", "shubh_hi_ecomm", "hi_question_shubh")

print("--- STT auto-detect")
stt(hi_q)
stt(en_path)

path = "data/sarvam_spend.json"
prev = json.load(open(path)) if os.path.exists(path) else {"spent_inr": 0.0, "log": []}
prev["spent_inr"] = round(prev["spent_inr"] + spend, 3)
prev["log"].append({"what": "smoke", "inr": round(spend, 3), "at": time.strftime("%H:%M")})
json.dump(prev, open(path, "w"), indent=1)
print(f"--- estimated spend this run Rs {spend:.2f}; total Rs {prev['spent_inr']}")
