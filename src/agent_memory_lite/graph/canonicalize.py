"""Canonical-name helpers.

Lower-cases, collapses whitespace, and strips outer punctuation. Used by
`upsert_entity` so "SQLite", "sqlite", and " sqlite " all hit the same row.
"""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")
_OUTER_PUNCT = re.compile(r"^[^\w]+|[^\w]+$")


def canonicalize_name(name: str) -> str:
    if not name:
        return ""
    cleaned = _WS.sub(" ", name).strip()
    cleaned = _OUTER_PUNCT.sub("", cleaned)
    return cleaned.lower()
