"""Smoke test: one forced tool call and one strict-schema call per LLM lane."""
import asyncio
import json
import os
import sys
import time
from typing import Literal

from dotenv import load_dotenv
from google import genai
from google.genai import types
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.llm.schema import strict_schema  # noqa: E402

load_dotenv()


class GetOrder(BaseModel):
    order_number: str = Field(description="Order number, e.g. RA-10234")


class Fact(BaseModel):
    kind: Literal["order_number", "amount", "date", "tracking_number"]
    value: str


class ReplyToCustomer(BaseModel):
    reply_en: str
    facts: list[Fact]
    sentiment: Literal["calm", "frustrated", "angry", "distressed"]
    confidence: Literal["low", "medium", "high"]
    escalation_reason: Literal["none", "legal_threat", "fraud_signal", "distress", "customer_requested_human"]


class Summary(BaseModel):
    issue: str
    summary: str
    mood: Literal["calm", "frustrated", "angry", "distressed"]
    next_step: str


TOOLS = {
    "get_order": ("Look up an order by its order number.", GetOrder),
    "reply_to_customer": ("Send the final reply to the customer and end the turn.", ReplyToCustomer),
}
SYSTEM = "You are a customer support agent for an online store. Act only through tools. Look up orders before answering."
USER = "<customer_message>Hi, where is my order RA-10234? It has been a week.</customer_message>"
SUMMARY_PROMPT = (
    "Summarize this support conversation for a human agent.\n"
    "customer: my blender arrived broken, order RA-10234, I paid Rs 1,499\n"
    "ai: sorry! I can refund Rs 1,499 and send a replacement. Shall I go ahead?\n"
    "customer: yes please, quickly"
)


def check_tool(name: str, args: dict) -> str:
    model = TOOLS[name][1]
    model.model_validate(args)
    return f"{name}({json.dumps(args)})"


async def gemini_lane(model: str) -> dict:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"].split(",")[0])
    decls = [types.FunctionDeclaration(name=n, description=d, parameters_json_schema=strict_schema(m)) for n, (d, m) in TOOLS.items()]
    out: dict = {"lane": f"gemini:{model}"}
    t = time.perf_counter()
    try:
        r = await client.aio.models.generate_content(
            model=model,
            contents=USER,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                tools=[types.Tool(function_declarations=decls)],
                tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY")),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        fc = r.function_calls[0]
        sig = any(p.thought_signature for p in r.candidates[0].content.parts if p.function_call)
        out["tool"] = check_tool(fc.name, dict(fc.args)) + f" thought_sig={sig}"
    except Exception as e:  # noqa: BLE001
        out["tool"] = f"FAIL {type(e).__name__}: {str(e)[:300]}"
    out["tool_s"] = round(time.perf_counter() - t, 2)
    t = time.perf_counter()
    try:
        r = await client.aio.models.generate_content(
            model=model,
            contents=SUMMARY_PROMPT,
            config=types.GenerateContentConfig(response_mime_type="application/json", response_json_schema=strict_schema(Summary)),
        )
        Summary.model_validate_json(r.text)
        out["schema"] = "OK " + r.text[:120]
    except Exception as e:  # noqa: BLE001
        out["schema"] = f"FAIL {type(e).__name__}: {str(e)[:300]}"
    out["schema_s"] = round(time.perf_counter() - t, 2)
    return out


def oa_tools() -> list[dict]:
    return [{"type": "function", "function": {"name": n, "description": d, "parameters": strict_schema(m)}} for n, (d, m) in TOOLS.items()]


async def openai_lane(provider: str, model: str, base_url: str, key: str, tool_runs: int = 1) -> dict:
    client = AsyncOpenAI(base_url=base_url, api_key=key, timeout=30)
    extra = {"provider": {"require_parameters": True}} if provider == "openrouter" else None
    out: dict = {"lane": f"{provider}:{model}"}
    results = []
    t = time.perf_counter()
    for _ in range(tool_runs):
        try:
            raw = await client.chat.completions.with_raw_response.create(
                model=model,
                messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}],
                tools=oa_tools(),
                tool_choice="required",
                extra_body=extra,
            )
            r = raw.parse()
            tc = r.choices[0].message.tool_calls[0]
            results.append("OK " + check_tool(tc.function.name, json.loads(tc.function.arguments)))
            out["headers"] = {k: v for k, v in raw.headers.items() if "ratelimit" in k.lower()}
        except Exception as e:  # noqa: BLE001
            results.append(f"FAIL {type(e).__name__}: {str(e)[:300]}")
    out["tool"] = results if tool_runs > 1 else results[0]
    out["tool_s"] = round((time.perf_counter() - t) / tool_runs, 2)
    t = time.perf_counter()
    try:
        r = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": SUMMARY_PROMPT}],
            response_format={"type": "json_schema", "json_schema": {"name": "summary", "strict": True, "schema": strict_schema(Summary)}},
            extra_body=extra,
        )
        Summary.model_validate_json(r.choices[0].message.content)
        out["schema"] = "OK " + r.choices[0].message.content[:120]
    except Exception as e:  # noqa: BLE001
        out["schema"] = f"FAIL {type(e).__name__}: {str(e)[:300]}"
    out["schema_s"] = round(time.perf_counter() - t, 2)
    return out


async def main() -> None:
    lanes = sys.argv[1:] or ["gemini", "groq", "openrouter"]
    jobs = []
    if "gemini" in lanes:
        jobs += [gemini_lane("gemini-3.8-flash"), gemini_lane("gemini-3.6-flash"), gemini_lane("gemini-3.5-flash-lite")]
    if "groq" in lanes:
        jobs.append(openai_lane("groq", "openai/gpt-oss-120b", "https://api.groq.com/openai/v1", os.environ["GROQ_API_KEY"], tool_runs=5))
    if "openrouter" in lanes:
        for m in os.environ.get("OR_CANDIDATES", "nvidia/nemotron-3.5-super-vl:free").split(","):
            assert m.endswith(":free"), "OpenRouter lane must use a :free model"
            jobs.append(openai_lane("openrouter", m, "https://openrouter.ai/api/v1", os.environ["OPENROUTER_API_KEY"]))
    for fut in asyncio.as_completed([asyncio.wait_for(j, 60) for j in jobs]):
        try:
            res = await fut
        except TimeoutError:
            res = {"lane": "?", "error": "timeout 60s"}
        print(json.dumps(res, ensure_ascii=False, indent=1), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
