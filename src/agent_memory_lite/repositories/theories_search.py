"""Search-side queries + ranking for theories.

Split out of ``theories_repo.py`` so the repo file stays under the
SLOC ceiling. Holds tokenization, search-text builder, ranker, and
the ``list_theories`` endpoint that joins capability_links text into
the search input.
"""

from __future__ import annotations

import re
import sqlite3

from agent_memory_lite.models.enums import (
    CapabilityLinkTargetType,
    TheoryStatus,
)
from agent_memory_lite.models.theories import Theory
from agent_memory_lite.repositories.capability_links_repo import capability_link_text_by_target
from agent_memory_lite.repositories.theories_search_rows import (
    _coerce_evidence_kind,
    row_to_evidence,
    row_to_theory,
)

__all__ = [
    "_coerce_evidence_kind",
    "list_theories",
    "row_to_evidence",
    "row_to_theory",
]

_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)
_ACTIVE_STATUSES = {s.value for s in TheoryStatus if s.value not in {"archived", "superseded"}}


def _searchable_text(theory: Theory, capability_text: str = "") -> str:
    parts = [
        theory.title,
        theory.domain,
        theory.claim,
        theory.mechanism or "",
        theory.experiment_plan or "",
        capability_text,
        " ".join(theory.predictions),
        " ".join(theory.validation_criteria),
        " ".join(theory.dependent_decision_ids),
        " ".join(theory.tags),
    ]
    return " ".join(parts).lower()


def _tokens(query: str | None) -> list[str]:
    if not query:
        return []
    return [token.lower() for token in _TOKEN_RE.findall(query) if len(token) > 1]


def _rank(theory: Theory, tokens: list[str], capability_text: str = "") -> tuple[float, str]:
    status_bonus = {
        TheoryStatus.TESTING: 0.25,
        TheoryStatus.VALIDATED: 0.25,
        TheoryStatus.SUPPORTED: 0.22,
        TheoryStatus.REJECTED: 0.18,
        TheoryStatus.PROPOSED: 0.15,
        TheoryStatus.WEAKENED: 0.08,
        TheoryStatus.SUPERSEDED: -0.30,
        TheoryStatus.ARCHIVED: -0.35,
    }[theory.status]
    text = _searchable_text(theory, capability_text)
    token_score = sum(1.0 for token in tokens if token in text)
    score = token_score + theory.importance + (theory.confidence * 0.5) + status_bonus
    return score, theory.updated_at


def list_theories(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    statuses: list[TheoryStatus] | None = None,
    limit: int = 20,
    include_archived: bool = False,
    since: str | None = None,
    until: str | None = None,
) -> list[Theory]:
    sql = "SELECT * FROM theories WHERE workspace_id = ?"
    params: list[object] = [workspace_id]
    if since:
        sql += " AND created_at >= ?"
        params.append(since)
    if until:
        sql += " AND created_at <= ?"
        params.append(until)
    rows = conn.execute(sql, params).fetchall()
    theories = [row_to_theory(row) for row in rows]
    linked_text = capability_link_text_by_target(
        conn,
        workspace_id=workspace_id,
        target_type=CapabilityLinkTargetType.THEORY,
        target_ids=[theory.id for theory in theories],
    )
    if statuses is not None:
        allowed = set(statuses)
        theories = [theory for theory in theories if theory.status in allowed]
    elif not include_archived:
        theories = [theory for theory in theories if theory.status.value in _ACTIVE_STATUSES]

    terms = _tokens(query)
    if terms:
        theories = [
            theory
            for theory in theories
            if any(
                token in _searchable_text(theory, linked_text.get(theory.id, "")) for token in terms
            )
        ]
    theories.sort(
        key=lambda theory: _rank(theory, terms, linked_text.get(theory.id, "")),
        reverse=True,
    )
    return theories[:limit]
