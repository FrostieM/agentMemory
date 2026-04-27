"""SQL operations for research theories and evidence."""

from __future__ import annotations

import json
import re
import sqlite3

from agent_memory_lite.models.enums import TheoryEvidenceKind, TheoryStatus
from agent_memory_lite.models.theories import Theory, TheoryEvidence

_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)
_ACTIVE_STATUSES = {
    TheoryStatus.PROPOSED.value,
    TheoryStatus.TESTING.value,
    TheoryStatus.SUPPORTED.value,
    TheoryStatus.VALIDATED.value,
    TheoryStatus.WEAKENED.value,
    TheoryStatus.REJECTED.value,
}


def _json_list(raw: str | None) -> list[str]:
    data = json.loads(raw or "[]")
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def _json_dict(raw: str | None) -> dict[str, object]:
    data = json.loads(raw or "{}")
    return data if isinstance(data, dict) else {}


def _row_to_theory(row: sqlite3.Row) -> Theory:
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


def _row_to_evidence(row: sqlite3.Row) -> TheoryEvidence:
    return TheoryEvidence(
        id=row["id"],
        workspace_id=row["workspace_id"],
        theory_id=row["theory_id"],
        kind=TheoryEvidenceKind(row["kind"]),
        summary=row["summary"],
        source_episode_id=row["source_episode_id"],
        artifact_path=row["artifact_path"],
        metrics=_json_dict(row["metrics_json"]),
        confidence=float(row["confidence"]),
        observed_at=row["observed_at"],
        created_at=row["created_at"],
    )


def insert_theory_row(
    conn: sqlite3.Connection,
    *,
    theory_id: str,
    workspace_id: str,
    title: str,
    domain: str,
    claim: str,
    mechanism: str | None,
    predictions: list[str],
    validation_criteria: list[str],
    experiment_plan: str | None,
    dependent_decision_ids: list[str],
    tags: list[str],
    status: TheoryStatus,
    supersedes_theory_id: str | None,
    source_episode_id: str | None,
    confidence: float,
    importance: float,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO theories (
            id, workspace_id, title, domain, claim, mechanism,
            predictions_json, validation_criteria_json, experiment_plan,
            dependent_decision_ids_json, tags_json, status,
            supersedes_theory_id, source_episode_id, confidence, importance,
            evidence_count, evidence_strength, created_at, updated_at, last_tested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0.0, ?, ?, NULL)
        """,
        (
            theory_id,
            workspace_id,
            title,
            domain,
            claim,
            mechanism,
            json.dumps(predictions, sort_keys=True),
            json.dumps(validation_criteria, sort_keys=True),
            experiment_plan,
            json.dumps(dependent_decision_ids, sort_keys=True),
            json.dumps(tags, sort_keys=True),
            status.value,
            supersedes_theory_id,
            source_episode_id,
            confidence,
            importance,
            created_at,
            created_at,
        ),
    )


def archive_theory(
    conn: sqlite3.Connection,
    *,
    theory_id: str,
    updated_at: str,
) -> None:
    conn.execute(
        """
        UPDATE theories
        SET status = 'superseded', updated_at = ?
        WHERE id = ?
        """,
        (updated_at, theory_id),
    )


def update_theory_confidence_status(
    conn: sqlite3.Connection,
    *,
    theory_id: str,
    confidence: float,
    status: TheoryStatus,
    updated_at: str,
    last_tested_at: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE theories
        SET confidence = ?,
            status = ?,
            updated_at = ?,
            last_tested_at = COALESCE(?, last_tested_at)
        WHERE id = ?
        """,
        (confidence, status.value, updated_at, last_tested_at, theory_id),
    )


def get_theory(conn: sqlite3.Connection, theory_id: str) -> Theory | None:
    row = conn.execute("SELECT * FROM theories WHERE id = ?", (theory_id,)).fetchone()
    return _row_to_theory(row) if row is not None else None


def _searchable_text(theory: Theory) -> str:
    parts = [
        theory.title,
        theory.domain,
        theory.claim,
        theory.mechanism or "",
        theory.experiment_plan or "",
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


def _rank(theory: Theory, tokens: list[str]) -> tuple[float, str]:
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
    text = _searchable_text(theory)
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
) -> list[Theory]:
    rows = conn.execute(
        "SELECT * FROM theories WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchall()
    theories = [_row_to_theory(row) for row in rows]
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
            if any(token in _searchable_text(theory) for token in terms)
        ]
    theories.sort(key=lambda theory: _rank(theory, terms), reverse=True)
    return theories[:limit]


def insert_theory_evidence_row(
    conn: sqlite3.Connection,
    *,
    evidence_id: str,
    workspace_id: str,
    theory_id: str,
    kind: TheoryEvidenceKind,
    summary: str,
    source_episode_id: str | None,
    artifact_path: str | None,
    metrics: dict[str, object],
    confidence: float,
    observed_at: str,
    created_at: str,
) -> None:
    strength_delta = {
        TheoryEvidenceKind.SUPPORTING: confidence,
        TheoryEvidenceKind.REFUTING: -confidence,
        TheoryEvidenceKind.MIXED: -0.5 * confidence,
        TheoryEvidenceKind.NEUTRAL: 0.0,
        TheoryEvidenceKind.EXPERIMENT: 0.0,
    }[kind]
    conn.execute(
        """
        INSERT INTO theory_evidence (
            id, workspace_id, theory_id, kind, summary, source_episode_id,
            artifact_path, metrics_json, confidence, observed_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evidence_id,
            workspace_id,
            theory_id,
            kind.value,
            summary,
            source_episode_id,
            artifact_path,
            json.dumps(metrics, sort_keys=True),
            confidence,
            observed_at,
            created_at,
        ),
    )
    conn.execute(
        """
        UPDATE theories
        SET updated_at = ?,
            evidence_count = evidence_count + 1,
            evidence_strength = evidence_strength + ?,
            last_tested_at = CASE
                WHEN ? IN ('supporting', 'refuting', 'mixed', 'experiment') THEN ?
                ELSE last_tested_at
            END
        WHERE id = ?
        """,
        (created_at, strength_delta, kind.value, observed_at, theory_id),
    )


def get_theory_evidence(conn: sqlite3.Connection, evidence_id: str) -> TheoryEvidence | None:
    row = conn.execute("SELECT * FROM theory_evidence WHERE id = ?", (evidence_id,)).fetchone()
    return _row_to_evidence(row) if row is not None else None


def list_evidence_for_theory(
    conn: sqlite3.Connection,
    theory_id: str,
    *,
    limit: int = 10,
) -> list[TheoryEvidence]:
    rows = conn.execute(
        """
        SELECT * FROM theory_evidence
        WHERE theory_id = ?
        ORDER BY observed_at DESC, created_at DESC
        LIMIT ?
        """,
        (theory_id, limit),
    ).fetchall()
    return [_row_to_evidence(row) for row in rows]
