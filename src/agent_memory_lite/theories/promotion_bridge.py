"""Theory to reviewable decision candidate bridge."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.models.candidates import MemoryCandidate, TemporalSpan
from agent_memory_lite.models.enums import (
    MemoryCandidateKind,
    MemoryCandidateStatus,
    TheoryEvidenceKind,
    TheoryStatus,
    TrustLevel,
)
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.candidates_repo import insert_candidate_row
from agent_memory_lite.repositories.theories_repo import get_theory
from agent_memory_lite.repositories.theory_evidence_repo import list_evidence_for_theory
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


@dataclass(frozen=True, slots=True)
class CandidateEmission:
    candidate_id: str
    theory_id: str
    evidence_count: int
    evidence_strength: float


def _has_open_candidate(conn: sqlite3.Connection, *, workspace_id: str, theory_id: str) -> bool:
    rows = conn.execute(
        """
        SELECT metadata_json FROM candidates
        WHERE workspace_id = ?
          AND kind = ?
          AND status IN (?, ?)
        """,
        (
            workspace_id,
            MemoryCandidateKind.DECISION.value,
            MemoryCandidateStatus.NEW.value,
            MemoryCandidateStatus.ACCEPTED.value,
        ),
    ).fetchall()
    for row in rows:
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        if isinstance(metadata, dict) and metadata.get("theory_id") == theory_id:
            return True
    return False


def _supporting_evidence_metrics(conn: sqlite3.Connection, *, theory_id: str) -> tuple[int, float]:
    rows = list_evidence_for_theory(conn, theory_id, limit=200)
    supporting = [row for row in rows if row.kind == TheoryEvidenceKind.SUPPORTING]
    count = len(supporting)
    if count == 0:
        return (0, 0.0)
    avg_confidence = sum(float(row.confidence or 0.0) for row in supporting) / count
    return (count, avg_confidence)


def maybe_emit_decision_candidate(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    theory_id: str,
    settings: Settings,
) -> CandidateEmission | None:
    """Emit a canonical decision candidate when bridge conditions are met."""
    if not settings.theory_bridge_enabled:
        return None
    theory = get_theory(conn, theory_id)
    if theory is None or theory.workspace_id != workspace_id:
        return None
    if theory.status != TheoryStatus.VALIDATED:
        return None
    evidence_count, evidence_strength = _supporting_evidence_metrics(conn, theory_id=theory_id)
    if evidence_count < settings.theory_bridge_min_evidence:
        return None
    if _has_open_candidate(conn, workspace_id=workspace_id, theory_id=theory_id):
        return None

    candidate_id = new_id(IdKind.MEMORY_CANDIDATE)
    now_iso = iso_now()
    proposed_title = f"Adopt: {theory.title}"[:200]
    proposed_text = theory.claim
    proposed_rationale = theory.mechanism or ""
    candidate = MemoryCandidate(
        kind=MemoryCandidateKind.DECISION,
        subject=proposed_title,
        predicate="should_promote_to_decision",
        object=proposed_text,
        evidence=proposed_rationale or proposed_text,
        confidence=theory.confidence,
        importance=theory.importance,
        trust_level=TrustLevel.AGENT_INFERRED,
        temporal=TemporalSpan(observed_at=now_iso, valid_from=now_iso),
        write_targets=["decision"],
        source_episode_id=theory.source_episode_id,
        metadata={
            "source": "theory_bridge",
            "theory_id": theory_id,
            "evidence_count": evidence_count,
            "evidence_strength": evidence_strength,
            "proposed_title": proposed_title,
            "proposed_rationale": proposed_rationale,
        },
    )
    insert_candidate_row(
        conn,
        candidate_id=candidate_id,
        workspace_id=workspace_id,
        candidate=candidate,
        status=MemoryCandidateStatus.NEW,
        created_at=now_iso,
    )
    insert_audit(
        conn,
        workspace_id=workspace_id,
        action="theory.candidate_decision_emitted",
        target_type="memory_candidate",
        target_id=candidate_id,
        source_episode_id=theory.source_episode_id,
        after={
            "theory_id": theory_id,
            "evidence_count": evidence_count,
            "evidence_strength": evidence_strength,
        },
    )
    return CandidateEmission(
        candidate_id=candidate_id,
        theory_id=theory_id,
        evidence_count=evidence_count,
        evidence_strength=evidence_strength,
    )
