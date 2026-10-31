"""Sarvam spend meter: estimates rupees from characters and audio seconds, persisted to data/sarvam_spend.json."""
import json
import os
import time
from typing import Any

from app import config

PATH = f"{config.ROOT}/data/sarvam_spend.json"
PRICE = {"translate_char": 2 / 1000, "lid_char": 3.5 / 10000, "stt_second": 0.49 / 60, "tts_char": 1 / 1000, "tts_v3_char": 3 / 1000}


def _load() -> dict[str, Any]:
    if os.path.exists(PATH):
        with open(PATH) as f:
            return json.load(f)
    return {"spent_inr": 0.0, "log": []}


_state = _load()


def add(kind: str, units: float) -> float:
    inr = PRICE[kind] * units
    _state["spent_inr"] = round(_state["spent_inr"] + inr, 4)
    _state.setdefault("by_kind", {})[kind] = round(_state.get("by_kind", {}).get(kind, 0) + inr, 4)
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w") as f:
        json.dump(_state, f, indent=1)
    return inr


def left() -> float:
    return config.SARVAM_BUDGET_INR - _state["spent_inr"]


def allowed(demo: bool = False) -> bool:
    """At Rs 10 left, all non-demo Sarvam use stops."""
    return left() > 0 and (demo or left() > config.SARVAM_STOP_AT_INR_LEFT)


def status() -> dict[str, Any]:
    return {"spent_inr": round(_state["spent_inr"], 2), "left_inr": round(left(), 2), "budget_inr": config.SARVAM_BUDGET_INR,
            "by_kind": _state.get("by_kind", {}), "updated": time.strftime("%H:%M")}
