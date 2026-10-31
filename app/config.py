"""Settings read once from .env. Everything tunable lives here."""
import os
import re

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


PRODUCT_NAME = env("PRODUCT_NAME", "ResolveAI")
DEBUG = env("DEBUG") == "1"
DEMO_MODE = env("DEMO_MODE") == "1"  # lets Sarvam run below the Rs 10 reserve

MONGO_URI = env("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = env("MONGO_DB", "resolveai")

AUTO_REFUND_LIMIT_INR = int(env("AUTO_REFUND_LIMIT_INR", "2000"))
MAX_IDENTITY_ATTEMPTS = 3
REPEAT_CONTACT_LIMIT = 3  # third contact in 30 days escalates

GEMINI_API_KEY = env("GEMINI_API_KEY")
GROQ_API_KEY = env("GROQ_API_KEY")
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY")
LLM_LANES = [env(f"LLM_LANE_{i}") for i in range(1, 9) if env(f"LLM_LANE_{i}")]
LLM_LIGHT_MODEL = env("LLM_LIGHT_MODEL", "gemini-3.5-flash-lite")
LLM_TIMEOUT_S = 10.0
# Free-tier requests/day per lane (AI Studio, Groq headers, OpenRouter key), shown in the footer meter.
DAILY_LIMITS = {"gemini:gemini-3.5-flash-lite": 500, "gemini:gemini-3.1-flash-lite": 500, "gemini:gemini-3.6-flash": 20,
                "gemini:gemini-3.8-flash": 20, "groq:openai/gpt-oss-120b": 1000, "openrouter:nvidia/nemotron-3.5-super-vl:free": 50}

SARVAM_API_KEY = env("SARVAM_API_KEY")
SARVAM_STT_MODEL = env("SARVAM_STT_MODEL", "saaras:v4")
SARVAM_TRANSLATE_MODEL = env("SARVAM_TRANSLATE_MODEL", "mayura:v1")
SARVAM_TTS_MODEL = env("SARVAM_TTS_MODEL", "bulbul:v4-flash")
SARVAM_BUDGET_INR = float(env("SARVAM_BUDGET_INR", "100"))
SARVAM_STOP_AT_INR_LEFT = float(env("SARVAM_STOP_AT_INR_LEFT", "10"))

TWILIO_ACCOUNT_SID = env("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = env("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = env("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

AGENTMAIL_API_KEY = env("AGENTMAIL_API_KEY")
AGENTMAIL_INBOX_ID = env("AGENTMAIL_INBOX_ID")
AGENTMAIL_WEBHOOK_SECRET = env("AGENTMAIL_WEBHOOK_SECRET")

PUBLIC_BASE_URL = env("PUBLIC_BASE_URL").rstrip("/")
DEMO_CUSTOMER_EMAIL = env("DEMO_CUSTOMER_EMAIL").lower()
DEMO_CUSTOMER_PHONE = re.sub(r"[^\d+]", "", env("DEMO_CUSTOMER_PHONE"))  # E.164, no spaces

# The 11 supported languages (mayura:v1). Anything else gets an English reply.
LANGUAGES = {
    "en-IN": "English", "hi-IN": "Hindi", "bn-IN": "Bengali", "gu-IN": "Gujarati", "kn-IN": "Kannada",
    "ml-IN": "Malayalam", "mr-IN": "Marathi", "pa-IN": "Punjabi", "od-IN": "Odia", "ta-IN": "Tamil", "te-IN": "Telugu",
}
# One bulbul:v4-flash persona per language; Malayalam and Odia have none on v4-flash (text reply).
TTS_PERSONAS = {
    "en-IN": "simran_en_customer", "hi-IN": "suchitra_hi_ecomm", "bn-IN": "roopa_bn_conversational",
    "gu-IN": "pooja_gu_customer", "kn-IN": "chaitra_kn_conversation", "mr-IN": "ishita_mr_conversational",
    "pa-IN": "anand_pa_customer", "ta-IN": "gokul_ta_narration", "te-IN": "kavitha_te_conversation",
}
TEXT_REPLY_CAP = 900
VOICE_REPLY_CAP = 250

HUMAN_AGENTS = ["Anurag", "Neha", "Vikram"]
