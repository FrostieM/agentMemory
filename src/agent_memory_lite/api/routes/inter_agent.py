"""GET /memory/export_beliefs + POST /memory/import_beliefs — v3.1 Vector 6.

Two endpoints for the inter-agent negotiation MVP:

* **GET /memory/export_beliefs** — read-only, returns active decisions
  as portable ``InterAgentBelief`` rows. The caller (operator or
  another agent) serializes the JSON, transmits it, and feeds it to
  the receiving workspace's import_beliefs endpoint.
* **POST /memory/import_beliefs** — takes a foreign agent's belief
  list + the local source_episode_id; detects same-title-different-body
  conflicts; persists each as a ``memory_candidate(kind=negotiation)``
  row for operator review.

Strict-isolation aware: import is a write surface (creates candidate
rows). Export is read-only across workspaces.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.api.deps import (
    DbDep,
    SettingsDep,
    ensure_workspace_readable,
    ensure_workspace_writable,
)
from agent_memory_lite.maintenance.inter_agent import (
    InterAgentBelief,
    export_workspace,
    import_beliefs,
)

router = APIRouter()


class BeliefDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    foreign_id: str
    title: str
    decision_text: str
    confidence: float
    outcome_score: float
    foreign_workspace_id: str


class ExportBeliefsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    beliefs: list[BeliefDTO]


class ImportBeliefsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    source_episode_id: str
    beliefs: list[BeliefDTO] = Field(default_factory=list)


class ImportBeliefsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    conflicts_landed: int


def _to_belief_dto(rows: list[InterAgentBelief]) -> list[BeliefDTO]:
    return [
        BeliefDTO(
            foreign_id=r.foreign_id,
            title=r.title,
            decision_text=r.decision_text,
            confidence=r.confidence,
            outcome_score=r.outcome_score,
            foreign_workspace_id=r.foreign_workspace_id,
        )
        for r in rows
    ]


def _dto_to_belief(dto: BeliefDTO) -> InterAgentBelief:
    return InterAgentBelief(
        foreign_id=dto.foreign_id,
        title=dto.title,
        decision_text=dto.decision_text,
        confidence=dto.confidence,
        outcome_score=dto.outcome_score,
        foreign_workspace_id=dto.foreign_workspace_id,
    )


@router.get("/memory/export_beliefs", response_model=ExportBeliefsResponse)
def export_beliefs_get(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
) -> ExportBeliefsResponse:
    """Read-only: return active decisions as portable beliefs."""
    ensure_workspace_readable(workspace_id, settings)
    rows = export_workspace(conn, workspace_id=workspace_id, limit=limit)
    return ExportBeliefsResponse(workspace_id=workspace_id, beliefs=_to_belief_dto(rows))


@router.post("/memory/import_beliefs", response_model=ImportBeliefsResponse)
def import_beliefs_post(
    body: ImportBeliefsRequest, conn: DbDep, settings: SettingsDep
) -> ImportBeliefsResponse:
    """Detect conflicts vs foreign beliefs; persist as memory_candidates.

    Write surface — strict isolation must hold so a foreign agent's
    POST cannot pollute another project's workspace.
    """
    ensure_workspace_writable(body.workspace_id, settings)
    foreign: list[InterAgentBelief] = [_dto_to_belief(b) for b in body.beliefs]
    n = import_beliefs(
        conn,
        workspace_id=body.workspace_id,
        beliefs=foreign,
        source_episode_id=body.source_episode_id,
    )
    return ImportBeliefsResponse(workspace_id=body.workspace_id, conflicts_landed=n)
