"""Protected facts: mask order numbers, amounts, dates and tracking numbers before translation, restore after,
and fall back to English if any fact did not survive. mayura:v1 was seen dropping digits from `RA-10234`."""
import re

PATTERNS = [
    r"RA-\d+(?:-R)?",  # order numbers (and reship orders)
    r"\b[A-Z]{2}\d{6,}[A-Z]{0,2}\b",  # tracking numbers, e.g. BD4471209IN
    r"(?:Rs\.?|INR|₹)\s?\d[\d,]*(?:\.\d+)?",  # amounts
    r"\b\d{4}-\d{2}-\d{2}\b",  # ISO dates
    r"\b\d{1,2}\s(?:January|February|March|April|May|June|July|August|September|October|November|December)(?:\s\d{4})?\b",
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s\d{1,2}(?:,?\s\d{4})?\b",
]


def protected(text: str, facts: list[str]) -> list[str]:
    """The facts the model listed plus anything matching the patterns, longest first."""
    found = {f for f in facts if f and f in text}
    for p in PATTERNS:
        found.update(m.group(0) for m in re.finditer(p, text))
    return sorted(found, key=len, reverse=True)


def mask(text: str, values: list[str]) -> tuple[str, dict[str, str]]:
    table = {}
    for i, v in enumerate(values, start=1):
        if v in text:
            token = f"{{F{i}}}"
            text = text.replace(v, token)
            table[token] = v
    return text, table


def unmask(text: str, table: dict[str, str]) -> tuple[str, list[str]]:
    """Restore placeholders. Returns (text, missing values)."""
    missing = []
    for token, value in table.items():
        if token in text:
            text = text.replace(token, value)
        else:
            missing.append(value)
    if re.search(r"\{F\d+\}", text):
        missing.append("unrestored placeholder")
    return text, missing
