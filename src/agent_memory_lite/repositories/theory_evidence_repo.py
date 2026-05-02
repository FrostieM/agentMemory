"""SQL operations for theory evidence rows.

Split out of ``theories_repo.py`` so the repo file stays under the
SLOC ceiling. Holds the evidence INSERT (which atomically updates
the parent theory's evidence_count + evidence_strength), the by-id
fetch, and the per-theory listing.
"""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.enums import TheoryEvidenceKind
from agent_memory_lite.models.theories import TheoryEvidence
from agent_memory_lite.repositories.theories_search import row_to_evidence


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
    return row_to_evidence(row) if row is not None else None


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
    return [row_to_evidence(row) for row in rows]
