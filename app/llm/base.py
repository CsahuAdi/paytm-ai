"""Provider-neutral types shared by the adapters and the router."""
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class ToolResult:
    call: ToolCall
    payload: dict[str, Any]


@dataclass(eq=False)
class LaneFailure(Exception):
    """Anything that should move the turn to the next lane: 429, 5xx, timeout, invalid structure."""
    reason: str
    status: int | None = None
    retry_after: float | None = None
    daily: bool = False
    detail: str = field(default="")

    def __str__(self) -> str:
        retry = f" retry_after={self.retry_after}s" if self.retry_after else ""
        return f"{self.reason} (status={self.status}{retry}) {self.detail[:200]}"


class Session(Protocol):
    """One agent turn on one lane. Keeps the provider-native history between steps."""

    async def step(self, results: list[ToolResult] | None) -> list[ToolCall]: ...


class Adapter(Protocol):
    def session(self, system: str, user: str, tools: list[ToolSpec]) -> Session: ...

    async def structured(self, prompt: str, model: type[BaseModel]) -> BaseModel: ...
