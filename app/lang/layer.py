"""Language edges. English inside; Sarvam converts at the boundary (filled in during the language block)."""
from dataclasses import dataclass, field


@dataclass
class InText:
    language: str
    text_en: str
    meta: dict = field(default_factory=dict)


@dataclass
class OutText:
    text: str
    language: str
    meta: dict = field(default_factory=dict)


async def to_english(text: str, pinned: str | None, modality: str) -> InText:
    return InText(language=pinned or "en-IN", text_en=text)


async def from_english(text_en: str, language: str, modality: str, facts: list[str]) -> OutText:
    return OutText(text=text_en, language="en-IN")
