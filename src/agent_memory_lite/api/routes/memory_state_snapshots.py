"""POST /memory/snapshot_save | snapshot_list | snapshot_diff."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent_memory_lite.api.deps import (
    DbDep,
    SettingsDep,
    ensure_workspace_readable,
    ensure_workspace_writable,
)
from agent_memory_lite.api.schemas.memory_state_snapshots import (
    DiffStateSnapshotsRequest,
    DiffStateSnapshotsResponse,
    ListStateSnapshotsRequest,
    ListStateSnapshotsResponse,
    SaveStateSnapshotRequest,
    StateSnapshotResponse,
)
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.ingestion.memory_state_snapshot_diff import diff_state_snapshots
from agent_memory_lite.ingestion.memory_state_snapshot_writer import capture_state_snapshot
from agent_memory_lite.models.memory_state_snapshots import MemoryStateSnapshot
from agent_memory_lite.repositories.memory_state_snapshots_repo import (
    get_state_snapshot,
    list_state_snapshots,
)

router = APIRouter()


def _to_response(snapshot: MemoryStateSnapshot) -> StateSnapshotResponse:
    return StateSnapshotResponse(
        snapshot_id=snapshot.id,
        workspace_id=snapshot.workspace_id,
        name=snapshot.name,
        taken_at=snapshot.taken_at,
        counts=snapshot.counts,
        metadata=snapshot.metadata,
        created_at=snapshot.created_at,
    )


@router.post("/memory/snapshot_save", response_model=StateSnapshotResponse)
def snapshot_save_route(
    body: SaveStateSnapshotRequest, conn: DbDep, settings: SettingsDep
) -> StateSnapshotResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/snapshot_save",
        operation="snapshot_save",
        label="Save memory snapshot",
        snippet=body.name or "",
    ) as trace:
        snapshot = capture_state_snapshot(
            conn,
            workspace_id=body.workspace_id,
            name=body.name,
            metadata=body.metadata,
        )
        trace.stage_done(
            "save",
            "Snapshot persisted",
            counts={"items": sum(snapshot.counts.values())},
        )
        return _to_response(snapshot)


@router.post("/memory/snapshot_list", response_model=ListStateSnapshotsResponse)
def snapshot_list_route(
    body: ListStateSnapshotsRequest, conn: DbDep, settings: SettingsDep
) -> ListStateSnapshotsResponse:
    ensure_workspace_readable(body.workspace_id, settings)
    snapshots = list_state_snapshots(conn, workspace_id=body.workspace_id, limit=body.limit)
    return ListStateSnapshotsResponse(snapshots=[_to_response(item) for item in snapshots])


@router.post("/memory/snapshot_diff", response_model=DiffStateSnapshotsResponse)
def snapshot_diff_route(
    body: DiffStateSnapshotsRequest, conn: DbDep, settings: SettingsDep
) -> DiffStateSnapshotsResponse:
    ensure_workspace_readable(body.workspace_id, settings)
    before = get_state_snapshot(conn, body.before_id)
    after = get_state_snapshot(conn, body.after_id)
    if before is None or after is None:
        missing = [
            label for label, value in (("before_id", before), ("after_id", after)) if value is None
        ]
        raise HTTPException(status_code=404, detail=f"snapshot not found: {', '.join(missing)}")
    diff = diff_state_snapshots(before, after)
    return DiffStateSnapshotsResponse(
        before_snapshot_id=diff.before_snapshot_id,
        after_snapshot_id=diff.after_snapshot_id,
        before_taken_at=diff.before_taken_at,
        after_taken_at=diff.after_taken_at,
        counts_delta=diff.counts_delta,
        added=diff.added,
        removed=diff.removed,
        changed=diff.changed,
    )
