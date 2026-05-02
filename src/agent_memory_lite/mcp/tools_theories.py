"""Theory + theory-evidence MCP tool handlers."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.ingestion.theory_writer import add_theory_evidence, write_theory
from agent_memory_lite.models.theories import TheoryEvidenceIn, TheoryIn
from agent_memory_lite.repositories.theories_repo import (
    list_evidence_for_theory,
    list_theories,
)


def memory_write_theory(
    *, conn: sqlite3.Connection, payload: dict[str, Any], **_kwargs: Any
) -> dict[str, Any]:
    theory = write_theory(conn, TheoryIn(**payload))
    return {
        "theory_id": theory.id,
        "status": theory.status.value,
        "confidence": theory.confidence,
        "importance": theory.importance,
        "evidence_count": theory.evidence_count,
        "evidence_strength": theory.evidence_strength,
    }


def memory_add_theory_evidence(
    *, conn: sqlite3.Connection, payload: dict[str, Any], **_kwargs: Any
) -> dict[str, Any]:
    evidence = add_theory_evidence(conn, TheoryEvidenceIn(**payload))
    return {
        "evidence_id": evidence.id,
        "theory_id": evidence.theory_id,
        "kind": evidence.kind.value,
        "observed_at": evidence.observed_at,
    }


def memory_list_theories(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    query: str | None = None,
    limit: int = 20,
    include_archived: bool = False,
    include_evidence: bool = False,
    evidence_limit: int = 3,
    **_kwargs: Any,
) -> dict[str, Any]:
    theories = list_theories(
        conn,
        workspace_id=workspace_id,
        query=query,
        limit=limit,
        include_archived=include_archived,
    )
    return {
        "theories": [
            {
                "theory_id": theory.id,
                "title": theory.title,
                "domain": theory.domain,
                "claim": theory.claim,
                "validation_criteria": theory.validation_criteria,
                "dependent_decision_ids": theory.dependent_decision_ids,
                "status": theory.status.value,
                "confidence": theory.confidence,
                "importance": theory.importance,
                "evidence_count": theory.evidence_count,
                "evidence_strength": theory.evidence_strength,
                "tags": theory.tags,
                "evidence": [
                    {
                        "evidence_id": evidence.id,
                        "kind": evidence.kind.value,
                        "summary": evidence.summary,
                        "confidence": evidence.confidence,
                        "observed_at": evidence.observed_at,
                    }
                    for evidence in (
                        list_evidence_for_theory(conn, theory.id, limit=evidence_limit)
                        if include_evidence
                        else []
                    )
                ],
            }
            for theory in theories
        ],
    }
