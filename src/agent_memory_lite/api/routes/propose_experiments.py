"""GET/POST /memory/propose_experiments — v3.1 Vector 1 surface.

Lets an operator (or UI / curl) trigger the proposal scanner without
waiting for the next brain-pass tick. Two verbs:

* **GET** — preview-only. Returns the list of proposals that the
  scanner WOULD emit, without writing them to ``candidates``.
  Use for /ui/review experimentation.
* **POST** — preview + persist. Scans and writes each proposal as a
  ``memory_candidate(kind=theory_proposal)`` row (idempotent via
  deterministic id). Returns the same payload plus how many landed.

Both honor the ``MEMORY_EXPERIMENT_PROPOSAL_*`` env flags so the
operator's tuning takes effect immediately.

Vector1-audit-2 M5: the response carries ``feature_enabled`` and
``available`` flags so disabled-flag and missing-table states are not
silently indistinguishable from a healthy-but-empty workspace.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.api.deps import (
    DbDep,
    SettingsDep,
    ensure_workspace_readable,
    ensure_workspace_writable,
)
from agent_memory_lite.api.workspace_routing import ensure_workspace_matches_db
from agent_memory_lite.maintenance.experiment_proposal import (
    find_proposal_candidates,
    is_enabled,
    persist_proposal,
)

router = APIRouter()


class ProposalDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insight_id: str
    proposal_text: str
    validation_criterion: str
    confidence: float
    source_episode_id: str


class ProposeExperimentsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    proposals: list[ProposalDTO]
    persisted: int = 0
    candidate_ids: list[str] = Field(default_factory=list)
    # Vector1-audit-2 M5: explicit "off" vs "no insights" disambiguation.
    feature_enabled: bool = True
    # True when the canonical ``insights`` table exists. Legacy-only DBs
    # (never ran canonical 0001_init.sql) return ``available=false`` so
    # the operator can tell "no proposals" from "scanner cannot run".
    available: bool = True


def _to_dto(proposals: list[Any]) -> list[ProposalDTO]:
    return [
        ProposalDTO(
            insight_id=p.insight_id,
            proposal_text=p.proposal_text,
            validation_criterion=p.validation_criterion,
            confidence=p.confidence,
            source_episode_id=p.source_episode_id,
        )
        for p in proposals
    ]


def _scan_with_availability(
    conn: Any, *, workspace_id: str, limit: int | None
) -> tuple[list[Any], bool]:
    """Run the scanner. Return (proposals, available).

    Vector1-audit H2 + audit-2 M5: the scanner re-raises
    ``OperationalError`` so brain_pass logs misconfigs — but at the
    HTTP boundary, a legacy-only DB with no ``insights`` table is a
    known-supported deployment topology. Catch it here and surface
    ``available=false`` so the operator sees a flag, not a 500.
    """
    try:
        return (
            find_proposal_candidates(conn, workspace_id=workspace_id, limit=limit),
            True,
        )
    except sqlite3.OperationalError:
        return [], False


@router.get("/memory/propose_experiments", response_model=ProposeExperimentsResponse)
def propose_experiments_get(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(min_length=1),
    limit: int | None = Query(default=None, ge=1, le=50),
) -> ProposeExperimentsResponse:
    """Preview: return what the scanner WOULD propose. No writes."""
    ensure_workspace_readable(workspace_id, settings)
    enabled = is_enabled()
    proposals, available = _scan_with_availability(conn, workspace_id=workspace_id, limit=limit)
    return ProposeExperimentsResponse(
        workspace_id=workspace_id,
        proposals=_to_dto(proposals),
        feature_enabled=enabled,
        available=available,
    )


@router.post("/memory/propose_experiments", response_model=ProposeExperimentsResponse)
def propose_experiments_post(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(min_length=1),
    limit: int | None = Query(default=None, ge=1, le=50),
) -> ProposeExperimentsResponse:
    """Preview + persist. Each proposal writes a ``memory_candidate``
    (idempotent). Returns the proposals plus the count of rows landed.

    Vector1-audit-2 H1: this IS a write surface — strict-isolation
    must hold or another project's chat could pollute this workspace.
    """
    ensure_workspace_writable(workspace_id, settings)
    ensure_workspace_matches_db(conn, workspace_id, settings)
    enabled = is_enabled()
    proposals, available = _scan_with_availability(conn, workspace_id=workspace_id, limit=limit)
    candidate_ids: list[str] = []
    if available and proposals:
        # Vector1-audit-2 M2: one BEGIN/COMMIT around the whole loop so a
        # mid-loop failure rolls back the entire batch (idempotency stays
        # well-defined even under concurrent POSTs).
        try:
            conn.execute("BEGIN IMMEDIATE")
            for proposal in proposals:
                candidate_id = persist_proposal(conn, workspace_id=workspace_id, proposal=proposal)
                if candidate_id:
                    candidate_ids.append(candidate_id)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return ProposeExperimentsResponse(
        workspace_id=workspace_id,
        proposals=_to_dto(proposals),
        persisted=len(candidate_ids),
        candidate_ids=candidate_ids,
        feature_enabled=enabled,
        available=available,
    )
