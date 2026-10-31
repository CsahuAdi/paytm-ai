"""Developer-written plugin tools: logic that pack config cannot express (conflict checks, generated IDs, several
collections, external systems). Each registers a manifest with @plugin_tool; a pack opts in with `plugins:` in
pack.yaml, and an admin grants it per pack in the Tools tab. Nothing is hot-loaded: modules are imported by the
explicit list in load_all(), and admins never upload code.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

Tier = Literal["allow", "confirm", "approve"]


@dataclass(frozen=True)
class PluginTool:
    name: str
    description: str
    params: type[BaseModel]
    effect: Literal["read", "write", "irreversible"]
    loosest_tier: Tier
    secrets: tuple[str, ...]
    check: Callable[..., Awaitable[Any]]  # (ctx_like, params: dict, member: dict) -> Decision
    run: Callable[..., Awaitable[dict[str, Any]]]  # (action: dict, member: dict) -> result dict
    idempotency: Callable[[dict[str, Any]], str]
    label: str = ""


REGISTRY: dict[str, PluginTool] = {}


def plugin_tool(*, name: str, description: str, params: type[BaseModel], effect: Literal["read", "write", "irreversible"],
                loosest_tier: Tier, idempotency: Callable[[dict[str, Any]], str], secrets: tuple[str, ...] = (),
                label: str = "") -> Callable[[type], type]:
    def wrap(cls: type) -> type:
        impl = cls()
        REGISTRY[name] = PluginTool(name, description, params, effect, loosest_tier, tuple(secrets), impl.check, impl.run,
                                    idempotency, label or name.replace("_", " ").capitalize())
        return cls
    return wrap


def load_all() -> dict[str, PluginTool]:
    from plugins import book_slot, kirana  # noqa: F401  (explicit list; add new plugins here after review)
    return REGISTRY
