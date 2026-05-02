"""POST /memory/review_queue + POST /memory/compact_trigger.

Two operator-focused read endpoints:
* ``review_queue`` — what does the operator need to act on right
  now (promote/reject candidates, resolve maintenance events).
* ``compact_trigger`` — probe the chunk count + stale ratio and
  emit a ``compaction_due`` maintenance event when overdue.
"""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.api.schemas.review_queue import (
    CompactTriggerRequest,
    CompactTriggerResponse,
    ReviewQueueItemResponse,
    ReviewQueueRequest,
    ReviewQueueResponse,
)
from agent_memory_lite.maintenance.compact_trigger import check_compaction_threshold
from agent_memory_lite.maintenance.review_queue import build_review_queue

router = APIRouter()


@router.post("/memory/review_queue", response_model=ReviewQueueResponse)
def review_queue_route(
    body: ReviewQueueRequest, conn: DbDep, settings: SettingsDep
) -> ReviewQueueResponse:
    ensure_workspace_readable(body.workspace_id, settings)
    payload = build_review_queue(
        conn,
        workspace_id=body.workspace_id,
        limit_per_kind=body.limit_per_kind,
    )
    return ReviewQueueResponse(
        workspace_id=str(payload["workspace_id"]),
        generated_at=str(payload["generated_at"]),
        counts={k: int(v) for k, v in payload["counts"].items()},
        items=[
            ReviewQueueItemResponse(
                action=item["action"],
                target_type=item["target_type"],
                target_id=item["target_id"],
                summary=item["summary"],
                severity=item["severity"],
                created_at=item["created_at"],
                details=item["details"],
            )
            for item in payload["items"]
        ],
    )


@router.post("/memory/compact_trigger", response_model=CompactTriggerResponse)
def compact_trigger_route(
    body: CompactTriggerRequest, conn: DbDep, settings: SettingsDep
) -> CompactTriggerResponse:
    # The probe is read-mostly (it only writes a maintenance event
    # when overdue), but treat it as a writable side effect through
    # ensure_workspace_readable so cross-workspace probes from
    # project mode are still allowed.
    ensure_workspace_readable(body.workspace_id, settings)
    result = check_compaction_threshold(
        conn,
        workspace_id=body.workspace_id,
        settings=settings,
    )
    return CompactTriggerResponse(
        enabled=bool(result["enabled"]),
        total=int(result["total"]),
        stale=int(result["stale"]),
        threshold=int(result["threshold"]),
        triggered=bool(result.get("triggered", False)),
        event_written=bool(result.get("event_written", False)),
    )
