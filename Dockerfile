FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY packs ./packs
COPY plugins ./plugins
COPY static ./static
COPY scripts ./scripts
RUN mkdir -p data

# Non-secret defaults (same as .env.example). Keys, MONGO_URI and PACK come from the host's environment.
ENV LLM_LANE_1=gemini:gemini-3.5-flash-lite \
    LLM_LANE_2=gemini:gemma-4-26b-a4b-it \
    LLM_LANE_3=gemini:gemini-3.1-flash-lite \
    LLM_LANE_4=gemini:gemini-3.6-flash \
    LLM_LANE_5=groq:openai/gpt-oss-120b \
    LLM_LANE_6=openrouter:nvidia/nemotron-3.5-super-vl:free \
    LLM_LIGHT_MODEL=gemini-3.1-flash-lite \
    LLM_TIMEOUT_S=20 \
    MONGO_DB=resolveai

# One worker: live updates and per-ticket locks live in process memory. The seed step runs first and only fills an empty database.
CMD ["sh", "-c", "python scripts/seed_if_empty.py && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
