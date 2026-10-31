"""Turn Pydantic models into flat, strict JSON schemas every LLM lane accepts."""
from typing import Any

from pydantic import BaseModel


def _inline(node: Any, defs: dict) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline(defs[node["$ref"].split("/")[-1]], defs)
        out = {k: _inline(v, defs) for k, v in node.items() if k not in ("title", "$defs", "default")}
        if out.get("type") == "object":
            out["additionalProperties"] = False
            out["required"] = list(out.get("properties", {}).keys())
        return out
    if isinstance(node, list):
        return [_inline(x, defs) for x in node]
    return node


def strict_schema(model: type[BaseModel]) -> dict:
    """Inline $refs, drop titles/defaults, require every field, forbid extra keys."""
    raw = model.model_json_schema()
    return _inline(raw, raw.get("$defs", {}))
