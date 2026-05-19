"""GET /memory/predictive_warnings — v3.1 Vector 5 surface.

Lets an operator preview the predictive-failure warnings the
heuristic would surface, without waiting for the next brain_pass
tick. Read-only — Vector 5 MVP does not persist the warnings yet
(brain_pass tallies the count; full persistence lands when the LLM
augmentation does).

Response carries ``feature_enabled`` + ``available`` flags so the
disabled-flag and missing-outcome-column states are distinguishable
from a healthy-but-empty workspace (symmetric with the
/memory/propose_experiments surface).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.maintenance.predictive_failure import (
    find_predictive_warnings,
    is_enabled,
)

router = APIRouter()


class PredictiveWarningDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fresh_decision_id: str
    failed_decision_id: str
    similarity: float
    reason_text: str


class PredictiveWarningsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    warnings: list[PredictiveWarningDTO]
    feature_enabled: bool = True
    # ``available=false`` signals legacy-only DB (outcome_score column
    # missing from ``decisions``) so operator sees the misconfig.
    available: bool = True


def _to_dto(warnings: list[Any]) -> list[PredictiveWarningDTO]:
    return [
        PredictiveWarningDTO(
            fresh_decision_id=w.fresh_decision_id,
            failed_decision_id=w.failed_decision_id,
            similarity=w.similarity,
            reason_text=w.reason_text,
        )
        for w in warnings
    ]


def _scan_with_availability(
    conn: Any, *, workspace_id: str, limit: int | None
) -> tuple[list[Any], bool]:
    """Run the scanner. Return (warnings, available).

    Missing ``outcome_score`` column on a legacy-only DB raises
    ``OperationalError``. We catch it here and surface
    ``available=false`` rather than 500-ing the route.
    """
    try:
        return (
            find_predictive_warnings(conn, workspace_id=workspace_id, limit=limit),
            True,
        )
    except sqlite3.OperationalError:
        return [], False


@router.get("/memory/predictive_warnings", response_model=PredictiveWarningsResponse)
def predictive_warnings_get(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(min_length=1),
    limit: int | None = Query(default=None, ge=1, le=50),
) -> PredictiveWarningsResponse:
    """Preview: return current predictive-failure warnings. No writes."""
    ensure_workspace_readable(workspace_id, settings)
    warnings, available = _scan_with_availability(conn, workspace_id=workspace_id, limit=limit)
    return PredictiveWarningsResponse(
        workspace_id=workspace_id,
        warnings=_to_dto(warnings),
        feature_enabled=is_enabled(),
        available=available,
    )
