"""Row -> model deserialization for theories search.

Split out of ``theories_search.py`` so that file stays under the SLOC
ceiling. Holds the pure ``sqlite3.Row`` -> dataclass mappers and their
JSON-column helpers, including the drift-tolerant enum coercion for
theory status and evidence kind.
"""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.enums import (
    TheoryEvidenceKind,
    TheoryStatus,
    coerce_enum,
)
from agent_memory_lite.models.theories import Theory, TheoryEvidence


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
        # v3.5 audit-followup: same drift-tolerance pattern as evidence kind.
        # An unknown future status (or pre-v3 'archived' before the enum
        # caught up) must not 500 every compact read call.
        status=coerce_enum(TheoryStatus, row["status"], TheoryStatus.PROPOSED),
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
    """Thin wrapper that pins the safe fallback for theory_evidence.

    See ``models.enums.coerce_enum`` for the underlying contract and
    the historical context (v3.4 ``autonomous_loop`` bypass that
    crashed compact reads with HTTP 500). ``NEUTRAL`` is
    the safest default — neither supports nor refutes a theory, so
    the UI / hygiene checks downgrade ranking but don't draw the
    wrong conclusion about a rogue row.
    """
    return coerce_enum(TheoryEvidenceKind, raw, TheoryEvidenceKind.NEUTRAL)


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
