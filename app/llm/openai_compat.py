"""Groq and OpenRouter through the OpenAI SDK: tool_choice=required and strict json_schema output."""
import json
from typing import Any

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.llm.base import LaneFailure, ToolCall, ToolResult, ToolSpec
from app.llm.keys import KeyRing
from app.llm.schema import strict_schema

BASE_URLS = {"groq": "https://api.groq.com/openai/v1", "openrouter": "https://openrouter.ai/api/v1"}


def _failure(e: Exception) -> LaneFailure:
    if isinstance(e, openai.APIStatusError):
        retry = e.response.headers.get("retry-after")
        text = str(e)
        daily = e.status_code == 429 and ("per day" in text.lower() or "RPD" in text or "free-models-per-day" in text)
        return LaneFailure("http", status=e.status_code, retry_after=float(retry) if retry else None, daily=daily, detail=text)
    if isinstance(e, openai.APITimeoutError):
        return LaneFailure("timeout", detail=str(e))
    return LaneFailure("error", detail=f"{type(e).__name__}: {e}")


class OpenAISession:
    def __init__(self, adapter: "OpenAICompatAdapter", system: str, user: str, tools: list[ToolSpec]) -> None:
        self.a = adapter
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        self.tools = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.schema}} for t in tools]

    async def step(self, results: list[ToolResult] | None) -> list[ToolCall]:
        for r in results or []:
            self.messages.append({"role": "tool", "tool_call_id": r.call.id, "content": json.dumps(r.payload, ensure_ascii=False)})
        resp = None
        for attempt in range(2):
            try:
                resp = await self.a.client().chat.completions.create(
                    model=self.a.model, messages=self.messages, tools=self.tools, tool_choice="required", **self.a.extra())
                break
            except openai.BadRequestError as e:
                # Groq rejects a hallucinated tool name server-side (tool_use_failed): that is the one repair.
                if attempt == 0 and ("tool_use_failed" in str(e) or "tool call validation failed" in str(e).lower()):
                    continue
                raise _failure(e) from e
            except Exception as e:  # noqa: BLE001
                raise _failure(e) from e
        assert resp is not None
        msg = resp.choices[0].message
        if not msg.tool_calls:
            raise LaneFailure("invalid", detail="no tool call in response")
        calls = []
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"__unparseable__": tc.function.arguments}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, args=args))
        self.messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls]})
        return calls


class OpenAICompatAdapter:
    def __init__(self, provider: str, model: str, keys: KeyRing) -> None:
        if provider == "openrouter" and not model.endswith(":free"):
            raise ValueError(f"OpenRouter lane must use a :free model, got {model}")
        self.provider, self.model, self.keys = provider, model, keys

    def client(self) -> AsyncOpenAI:
        return AsyncOpenAI(base_url=BASE_URLS[self.provider], api_key=self.keys.next(), max_retries=0, timeout=30)

    def extra(self) -> dict[str, Any]:
        if self.provider == "openrouter":
            return {"extra_body": {"provider": {"require_parameters": True}}}
        if self.model.startswith("openai/gpt-oss"):
            return {"reasoning_effort": "low"}
        return {}

    def session(self, system: str, user: str, tools: list[ToolSpec]) -> OpenAISession:
        return OpenAISession(self, system, user, tools)

    async def structured(self, prompt: str, model: type[BaseModel]) -> BaseModel:
        try:
            resp = await self.client().chat.completions.create(
                model=self.model, messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_schema", "json_schema": {"name": model.__name__, "strict": True, "schema": strict_schema(model)}},
                **self.extra())
        except Exception as e:  # noqa: BLE001
            raise _failure(e) from e
        try:
            return model.model_validate_json(resp.choices[0].message.content or "")
        except ValidationError as e:
            raise LaneFailure("invalid", detail=str(e)) from e
