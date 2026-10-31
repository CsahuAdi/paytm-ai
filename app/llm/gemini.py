"""Gemini adapter (google-genai): forced function calling (mode ANY) and JSON-schema output."""
import json
import re
import uuid

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from app.llm.base import LaneFailure, ToolCall, ToolResult, ToolSpec
from app.llm.keys import KeyRing
from app.llm.schema import strict_schema


def _failure(e: Exception) -> LaneFailure:
    if isinstance(e, errors.APIError):
        raw = json.dumps(getattr(e, "details", None) or str(e), default=str)
        quota = sorted(set(re.findall(r'"quotaId": "([^"]+)"', raw)))
        delay = re.search(r'"retryDelay": "(\d+(?:\.\d+)?)s"', raw)
        daily = e.code == 429 and any("PerDay" in q for q in quota)
        detail = f"quota={quota} " if quota else ""
        return LaneFailure("http", status=e.code, daily=daily, retry_after=float(delay.group(1)) if delay else None,
                           detail=detail + str(e)[:150])
    return LaneFailure("error", detail=f"{type(e).__name__}: {e}")


class GeminiSession:
    def __init__(self, client: genai.Client, model: str, system: str, user: str, tools: list[ToolSpec]) -> None:
        self.client, self.model = client, model
        self.contents: list[types.Content] = [types.Content(role="user", parts=[types.Part.from_text(text=user)])]
        self.config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.schema) for t in tools])],
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.ANY)),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
        )

    async def step(self, results: list[ToolResult] | None) -> list[ToolCall]:
        if results:
            self.contents.append(types.Content(role="user", parts=[
                types.Part(function_response=types.FunctionResponse(id=r.call.id, name=r.call.name, response=r.payload))
                for r in results]))
        try:
            resp = await self.client.aio.models.generate_content(model=self.model, contents=self.contents, config=self.config)
        except Exception as e:  # noqa: BLE001
            raise _failure(e) from e
        content = resp.candidates[0].content if resp.candidates else None
        if content is None or not resp.function_calls:
            raise LaneFailure("invalid", detail="no function call in response")
        self.contents.append(content)  # verbatim, so thought signatures go back with the next step
        return [ToolCall(id=fc.id or uuid.uuid4().hex[:8], name=fc.name or "", args=dict(fc.args or {})) for fc in resp.function_calls]


class GeminiAdapter:
    def __init__(self, model: str, keys: KeyRing) -> None:
        self.model, self.keys = model, keys

    def _client(self) -> genai.Client:
        return genai.Client(api_key=self.keys.next())

    def session(self, system: str, user: str, tools: list[ToolSpec]) -> GeminiSession:
        return GeminiSession(self._client(), self.model, system, user, tools)

    async def structured(self, prompt: str, model: type[BaseModel]) -> BaseModel:
        try:
            resp = await self._client().aio.models.generate_content(
                model=self.model, contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json", response_json_schema=strict_schema(model)))
        except Exception as e:  # noqa: BLE001
            raise _failure(e) from e
        try:
            return model.model_validate_json(resp.text or "")
        except ValidationError as e:
            raise LaneFailure("invalid", detail=str(e)) from e
