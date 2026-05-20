"""Search-side queries + ranking for theories.

Split out of ``theories_repo.py`` so the repo file stays under the
SLOC ceiling. Holds tokenization, search-text builder, ranker, and
the ``list_theories`` endpoint that joins capability_links text into
the search input.
"""

from __future__ import annotations

import json
import re
import sqlite3

from agent_memory_lite.models.enums import (
    CapabilityLinkTargetType,
    TheoryEvidenceKind,
    TheoryStatus,
)
from agent_memory_lite.models.theories import Theory, TheoryEvidence
from agent_memory_lite.repositories.capability_links_repo import capability_link_text_by_target

_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)
_ACTIVE_STATUSES = {s.value for s in TheoryStatus if s.value not in {"archived", "superseded"}}


def _json_list(raw: str | None) -> list[str]:
    data = json.loads(raw or "[]")
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def _json_dict(raw: str | None) -> dict[str, object]:
    data = json.loads(raw or "{}")
    return data if isinstance(data, dict) else {}


def row_to_theory(row: sqlite3.Row) -> Theory:
    return Theory(
        id=row["id"],
        workspace_id=row["workspace_id"],
        title=row["title"],
        domain=row["domain"],
        claim=row["claim"],
        mechanism=row["mechanism"],
        predictions=_json_list(row["predictions_json"]),
        validation_criteria=_json_list(row["validation_criteria_json"]),
        experiment_plan=row["experiment_plan"],
        dependent_decision_ids=_json_list(row["dependent_decision_ids_json"]),
        tags=_json_list(row["tags_json"]),
        status=TheoryStatus(row["status"]),
        supersedes_theory_id=row["supersedes_theory_id"],
        source_episode_id=row["source_episode_id"],
        confidence=float(row["confidence"]),
        importance=float(row["importance"]),
        evidence_count=int(row["evidence_count"]),
        evidence_strength=float(row["evidence_strength"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_tested_at=row["last_tested_at"],
    )


def _coerce_evidence_kind(raw: object) -> TheoryEvidenceKind:
    """Convert a raw DB ``kind`` string into the enum, tolerating
    unknown values.

    Historical context: v3.4 ``cognition/autonomous_loop.py`` bypassed
    the ``add_theory_evidence`` repo helper and INSERTed a literal
    ``'autonomous_corroboration'`` string before that value existed in
    ``TheoryEvidenceKind``. Once a single such row landed, every
    ``/memory/get_context`` that gathered the parent theory raised
    ``ValueError`` and the route returned HTTP 500.

    Two defenses now cover that class of bug:

    1. The canonical value is registered in the enum so legitimate
       rows round-trip cleanly.
    2. This helper falls back to ``NEUTRAL`` for any other unknown
       string a future raw-SQL writer might introduce, instead of
       letting the exception escape the read path.
    """
    if isinstance(raw, TheoryEvidenceKind):
        return raw
    try:
        return TheoryEvidenceKind(str(raw))
    except ValueError:
        return TheoryEvidenceKind.NEUTRAL


def row_to_evidence(row: sqlite3.Row) -> TheoryEvidence:
    return TheoryEvidence(
        id=row["id"],
        workspace_id=row["workspace_id"],
        theory_id=row["theory_id"],
        kind=_coerce_evidence_kind(row["kind"]),
        summary=row["summary"],
        source_episode_id=row["source_episode_id"],
        artifact_path=row["artifact_path"],
        metrics=_json_dict(row["metrics_json"]),
        confidence=float(row["confidence"]),
        observed_at=row["observed_at"],
        created_at=row["created_at"],
    )


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
