"""Theory -> decision-candidate bridge.

When a theory is in ``status='validated'`` and has accumulated at least
``MEMORY_THEORY_BRIDGE_MIN_EVIDENCE`` supporting evidence rows, write a
proposed decision into ``decision_candidates``. The bridge NEVER touches
the ``decisions`` table — only the operator's explicit promote call
through the API can do that, preserving the trust-gate invariant.

Idempotency: a partial unique index in migration 0023 enforces "at most
one pending candidate per theory". Re-running this function on the same
theory is a no-op until the existing pending candidate is decided.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.models.enums import TheoryEvidenceKind, TheoryStatus
from agent_memory_lite.repositories.audit_repo import insert_audit
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


def _has_open_candidate(conn: sqlite3.Connection, theory_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM decision_candidates
        WHERE theory_id = ? AND status = 'pending'
        LIMIT 1
        """,
        (theory_id,),
    ).fetchone()
    return row is not None


def _supporting_evidence_metrics(conn: sqlite3.Connection, *, theory_id: str) -> tuple[int, float]:
    rows = list_evidence_for_theory(conn, theory_id, limit=200)
    supporting = [row for row in rows if row.kind == TheoryEvidenceKind.SUPPORTING]
    count = len(supporting)
    if count == 0:
        return (0, 0.0)
    avg_confidence = sum(row.confidence for row in supporting) / count
    return (count, avg_confidence)


def maybe_emit_decision_candidate(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    theory_id: str,
    settings: Settings,
) -> CandidateEmission | None:
    """Emit a decision candidate when conditions are met. Returns None when
    the bridge is disabled, the theory is missing or not validated, or the
    evidence threshold isn't reached yet.
    """
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
    if _has_open_candidate(conn, theory_id):
        return None
    candidate_id = new_id(IdKind.DECISION_CANDIDATE)
    now_iso = iso_now()
    proposed_title = f"Adopt: {theory.title}"[:200]
    proposed_text = theory.claim
    proposed_rationale = theory.mechanism or ""
    conn.execute(
        """
        INSERT INTO decision_candidates
        (id, workspace_id, theory_id, proposed_title, proposed_decision_text,
         proposed_rationale, evidence_count, evidence_strength, confidence,
         status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (
            candidate_id,
            workspace_id,
            theory_id,
            proposed_title,
            proposed_text,
            proposed_rationale,
            evidence_count,
            evidence_strength,
            theory.confidence,
            now_iso,
            now_iso,
        ),
    )
    insert_audit(
        conn,
        workspace_id=workspace_id,
        action="theory.candidate_decision_emitted",
        target_type="decision_candidate",
        target_id=candidate_id,
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
