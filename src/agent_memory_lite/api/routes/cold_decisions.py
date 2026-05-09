"""GET /memory/cold_decisions — decisions that haven't been retrieved recently.

1.3.0: complements ``/memory/cold_candidates`` (which targets every kind).
Cold decisions are active decisions whose ``last_retrieved_at`` is older
than the cutoff (or never set). Operator candidates for archive review:
either the rule is no longer relevant or retrieval ranking has lost it.

Read-only; never mutates. The result is a hint for the operator to call
``memory_archive`` on items that no longer matter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable

router = APIRouter()


class ColdDecisionRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_id: str
    title: str
    importance: float
    last_retrieved_at: str | None
    days_cold: int  # 9999 when never retrieved
    pinned: bool


class ColdDecisionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    cutoff_days: int
    total_active: int
    cold_count: int
    rows: list[ColdDecisionRow]


@router.get("/memory/cold_decisions", response_model=ColdDecisionsResponse)
def cold_decisions_route(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(default="default"),
    cutoff_days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=500),
) -> ColdDecisionsResponse:
    ensure_workspace_readable(workspace_id, settings)
    now = datetime.now(UTC)
    cutoff_iso = (now - timedelta(days=cutoff_days)).isoformat()

    total_active = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE workspace_id=? AND status='active'",
        (workspace_id,),
    ).fetchone()[0]

    # Pinned items intentionally included so the operator sees them; they
    # are nominated but the operator should usually NOT archive a pinned
    # decision. The ``pinned`` flag in each row makes this visible.
    rows = conn.execute(
        """
        SELECT id, title, importance, last_retrieved_at, COALESCE(pinned, 0) AS pinned
        FROM decisions
        WHERE workspace_id = ?
          AND status = 'active'
          AND (last_retrieved_at IS NULL OR last_retrieved_at < ?)
        ORDER BY
          CASE WHEN last_retrieved_at IS NULL THEN 0 ELSE 1 END,
          last_retrieved_at ASC,
          importance DESC
        LIMIT ?
        """,
        (workspace_id, cutoff_iso, limit),
    ).fetchall()

    out_rows: list[ColdDecisionRow] = []
    for r in rows:
        last_at = r["last_retrieved_at"]
        if last_at is None:
            days_cold = 9999
        else:
            try:
                last_dt = datetime.fromisoformat(str(last_at).replace("Z", "+00:00"))
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=UTC)
                days_cold = max(0, (now - last_dt).days)
            except (ValueError, TypeError):
                days_cold = 9999
        out_rows.append(
            ColdDecisionRow(
                decision_id=str(r["id"]),
                title=str(r["title"] or ""),
                importance=float(r["importance"]),
                last_retrieved_at=str(last_at) if last_at else None,
                days_cold=days_cold,
                pinned=bool(r["pinned"]),
            )
        )

    return ColdDecisionsResponse(
        workspace_id=workspace_id,
        cutoff_days=cutoff_days,
        total_active=int(total_active),
        cold_count=len(out_rows),
        rows=out_rows,
    )
