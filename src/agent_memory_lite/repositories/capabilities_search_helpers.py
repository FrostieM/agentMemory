"""Shared search helpers for capability repos.

Split out so each per-kind repo (roles / skills / playbooks) imports
the same tokenizer + filter without duplicating code.
"""

from __future__ import annotations

import json
import re

_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)


def json_list(raw: str | None) -> list[str]:
    data = json.loads(raw or "[]")
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def tokens_from(query: str | None) -> list[str]:
    if not query:
        return []
    return [token.lower() for token in _TOKEN_RE.findall(query) if len(token) > 1]


def contains_any(text: str, tokens: list[str]) -> bool:
    if not tokens:
        return True
    lower = text.lower()
    return any(token in lower for token in tokens)
