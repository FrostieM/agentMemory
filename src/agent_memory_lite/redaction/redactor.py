"""Compose pattern, keyword, and (optional) PII rules into a single redact call.

The output text replaces every matched span with `<<REDACTED:KIND>>`. Spans are
reported against the *original* text so callers can tell what was redacted and
where without ever holding the secret value.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.redaction.patterns import SHAPE_RULES
from agent_memory_lite.redaction.pii import PII_RULES
from agent_memory_lite.redaction.secret_keywords import KEYWORD_RULES

REDACTION_MARKER_PREFIX = "<<REDACTED:"


class RedactionSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int
    end: int
    kind: str


class RedactedText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    spans: list[RedactionSpan] = Field(default_factory=list)
    kinds_seen: list[str] = Field(default_factory=list)


def _all_rules(include_pii: bool) -> tuple[tuple[str, re.Pattern[str]], ...]:
    rules = SHAPE_RULES + KEYWORD_RULES
    return rules + PII_RULES if include_pii else rules


def _find_matches(text: str, include_pii: bool) -> list[tuple[int, int, str]]:
    matches: list[tuple[int, int, str]] = []
    for kind, pattern in _all_rules(include_pii):
        for match in pattern.finditer(text):
            matches.append((match.start(), match.end(), kind))
    return matches


def _resolve_overlaps(matches: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    matches.sort(key=lambda m: (m[0], -(m[1] - m[0])))
    chosen: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, kind in matches:
        if start >= last_end:
            chosen.append((start, end, kind))
            last_end = end
    return chosen


def redact(text: str, *, include_pii: bool = False) -> RedactedText:
    if not text:
        return RedactedText(text=text)

    chosen = _resolve_overlaps(_find_matches(text, include_pii))
    if not chosen:
        return RedactedText(text=text)

    parts: list[str] = []
    cursor = 0
    spans: list[RedactionSpan] = []
    kinds: list[str] = []
    for start, end, kind in chosen:
        parts.append(text[cursor:start])
        parts.append(f"{REDACTION_MARKER_PREFIX}{kind.upper()}>>")
        spans.append(RedactionSpan(start=start, end=end, kind=kind))
        if kind not in kinds:
            kinds.append(kind)
        cursor = end
    parts.append(text[cursor:])

    return RedactedText(text="".join(parts), spans=spans, kinds_seen=kinds)
