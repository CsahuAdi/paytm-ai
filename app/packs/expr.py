"""Condition grammar and value templates for packs. Plain data walking; nothing is ever eval()'d.

Scopes: params.* (validated, typed), session.* (engine-set), <alias>.* (the target document), after.*.
A string that is exactly "{path}" becomes the typed value; other strings interpolate with str().
"""
import operator
import re
from datetime import date, timedelta
from typing import Any

from app.packs.spec import Condition

WHOLE = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_.]*)\}$")
PART = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}")
COMPARE = {"==": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def resolve(path: str, scopes: dict[str, Any]) -> Any:
    node: Any = scopes
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def fill(template: Any, scopes: dict[str, Any]) -> Any:
    if isinstance(template, str):
        m = WHOLE.match(template)
        if m:
            return resolve(m.group(1), scopes)
        return PART.sub(lambda mm: "" if (v := resolve(mm.group(1), scopes)) is None else str(v), template)
    if isinstance(template, dict):
        if set(template) == {"days_after", "days"}:  # the one computed value: an ISO date plus N days
            base = resolve(str(template["days_after"]), scopes)
            start = date.fromisoformat(base) if isinstance(base, str) else date.today()
            return (start + timedelta(days=int(template["days"]))).isoformat()
        return {k: fill(v, scopes) for k, v in template.items()}
    if isinstance(template, list):
        return [fill(v, scopes) for v in template]
    return template


def holds(c: Condition, scopes: dict[str, Any]) -> bool:
    if c.all is not None:
        return all(holds(x, scopes) for x in c.all)
    if c.any is not None:
        return any(holds(x, scopes) for x in c.any)
    if c.not_ is not None:
        return not holds(c.not_, scopes)
    actual = resolve(c.field or "", scopes)
    if c.op == "exists":
        return actual is not None
    if c.op == "missing":
        return actual is None
    if c.op in ("in", "not_in"):
        found = actual in (c.value or [])
        return found if c.op == "in" else not found
    if actual is None:
        return c.op == "!="  # a missing value equals nothing and orders against nothing
    try:
        return bool(COMPARE[c.op](actual, c.value))
    except TypeError:
        return False
