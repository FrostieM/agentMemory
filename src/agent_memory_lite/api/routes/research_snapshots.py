"""Snapshot route.

Experiment + result routes live in ``research_experiments.py``;
mounted on the same prefix from the parent ``research.py``.
"""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_writable
from agent_memory_lite.api.routes.research_experiments import router as experiments_router
from agent_memory_lite.api.routes.research_responses import to_snapshot_response
from agent_memory_lite.api.schemas.research import (
    MemorySnapshotResponse,
    RegisterSnapshotRequest,
)
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.ingestion.research_writer import register_snapshot
from agent_memory_lite.models.research import MemorySnapshotIn

router = APIRouter()
router.include_router(experiments_router)


@router.post("/memory/register_snapshot", response_model=MemorySnapshotResponse)
def register_snapshot_route(
    body: RegisterSnapshotRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> MemorySnapshotResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/register_snapshot",
        operation="register_snapshot",
        label="Register snapshot",
        snippet=body.title,
    ) as trace:
        trace.stage_done("validate", "Snapshot payload accepted", snippet=body.snapshot_key)
        trace.stage_started("persist", "Persist snapshot")
        snapshot = register_snapshot(
            conn,
            MemorySnapshotIn(
                workspace_id=body.workspace_id,
                snapshot_key=body.snapshot_key,
                title=body.title,
                source=body.source,
                db_path=body.db_path,
                duckdb_path=body.duckdb_path,
                parquet_dir=body.parquet_dir,
                window_start=body.window_start,
                window_end=body.window_end,
                build_sha=body.build_sha,
                build_branch=body.build_branch,
                build_time=body.build_time,
                remote_host=body.remote_host,
                table_counts=body.table_counts,
                total_rows=body.total_rows,
                metadata=body.metadata,
                source_episode_id=body.source_episode_id,
            ),
        )
        trace.stage_done(
            "persist", "Snapshot persisted", counts={"total_rows": snapshot.total_rows}
        )
        trace.graph_delta(
            object_type="snapshot",
            object_id=snapshot.id,
            action="created",
            label="Snapshot registered",
        )
        response = to_snapshot_response(snapshot)
        trace.stage_done("response", "Snapshot response ready", counts={"snapshot_id": snapshot.id})
        return response
