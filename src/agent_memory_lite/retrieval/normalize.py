"""Light-weight query normalization.

Pulls out paths, identifier-style tokens, and exception names from the raw
query so downstream steps can boost relevant chunks. Phase 2 keeps this
intentionally simple — Phase 4+ may layer on tree-sitter aware extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PATH_RE = re.compile(r"\b(?:[\w.\-]+/)+[\w.\-]+\b")
_SYMBOL_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\([^)]*\))?\b")
_ERROR_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:Error|Exception|Failure)\b")


@dataclass(frozen=True, slots=True)
class NormalizedQuery:
    raw: str
    paths: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def normalize(query: str) -> NormalizedQuery:
    paths = _PATH_RE.findall(query)
    errors = _ERROR_RE.findall(query)
    symbols = [
        token
        for token in _SYMBOL_RE.findall(query)
        if "_" in token or any(ch.isupper() for ch in token[1:])
    ]
    seen: set[str] = set()
    unique_symbols: list[str] = []
    for token in symbols:
        if token not in seen:
            seen.add(token)
            unique_symbols.append(token)
    return NormalizedQuery(
        raw=query.strip(),
        paths=list(dict.fromkeys(paths)),
        symbols=unique_symbols,
        errors=list(dict.fromkeys(errors)),
    )
