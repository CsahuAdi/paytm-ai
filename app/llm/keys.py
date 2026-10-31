"""Round-robin over a comma-separated key list. One key per provider is the plan; a list is only tolerated."""
import itertools


class KeyRing:
    def __init__(self, raw: str) -> None:
        self.keys = [k.strip() for k in raw.split(",") if k.strip()]
        self._cycle = itertools.cycle(self.keys) if self.keys else None

    def next(self) -> str:
        if self._cycle is None:
            raise RuntimeError("no API key configured")
        return next(self._cycle)
