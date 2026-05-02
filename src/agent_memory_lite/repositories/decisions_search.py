"""Search + range helpers used by decisions_repo.

Pulled out of ``decisions_repo.py`` so that file stays under the
SLOC ceiling once pinned-first ordering and since/until date filters
land on ``list_active_decisions`` / ``list_all_decisions``.
"""

from __future__ import annotations

import re

from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.utils.sql_filters import date_range_clause

__all__ = ["date_range_clause", "filter_rank_limit"]

_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)


def _tokens(query: str | None) -> list[str]:
    if not query:
        return []
    return [token.lower() for token in _TOKEN_RE.findall(query) if len(token) > 1]


def _searchable_text(decision: Decision) -> str:
    return " ".join([decision.title, decision.decision_text, decision.rationale or ""]).lower()


def _rank(decision: Decision, tokens: list[str]) -> tuple[int, float, str]:
    """Rank tuple: pinned-flag (0/1), then content score, then updated_at.
    Pinned items always sort to the top regardless of token match."""
    text = _searchable_text(decision)
    token_score = sum(1.0 for token in tokens if token in text)
    score = token_score + decision.importance + (decision.confidence * 0.25)
    return (1 if decision.pinned else 0, score, decision.updated_at)


def filter_rank_limit(
    decisions: list[Decision],
    *,
    query: str | None,
    limit: int | None,
) -> list[Decision]:
    terms = _tokens(query)
    decisions.sort(key=lambda decision: _rank(decision, terms), reverse=True)
    return decisions if limit is None else decisions[:limit]
