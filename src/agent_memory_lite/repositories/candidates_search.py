"""Ranking helpers for the candidate review queue.

Split out of ``candidates_repo.py`` so the SQL CRUD and the search /
score logic stay one-concern-per-module. The repo keeps the
``list_candidates`` entrypoint and delegates filtering and ordering to
the helpers here.
"""

from __future__ import annotations

import re

from agent_memory_lite.models.candidates import StoredMemoryCandidate
from agent_memory_lite.models.enums import MemoryCandidateStatus

_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)


def tokenize_query(query: str | None) -> list[str]:
    """Split a freeform query into lowercase 2+-char tokens."""
    if not query:
        return []
    return [token.lower() for token in _TOKEN_RE.findall(query) if len(token) > 1]


def searchable_text(candidate: StoredMemoryCandidate) -> str:
    return " ".join(
        [
            candidate.kind.value,
            candidate.subject,
            candidate.predicate,
            candidate.object or "",
            candidate.evidence,
        ]
    ).lower()


def rank_candidate(candidate: StoredMemoryCandidate, tokens: list[str]) -> tuple[float, str]:
    """Score a candidate. Higher is better; ties broken by updated_at desc."""
    text = searchable_text(candidate)
    token_score = sum(1.0 for token in tokens if token in text)
    status_bonus = 0.2 if candidate.status == MemoryCandidateStatus.NEW else 0.0
    score = token_score + candidate.importance + (candidate.confidence * 0.25) + status_bonus
    return score, candidate.updated_at
