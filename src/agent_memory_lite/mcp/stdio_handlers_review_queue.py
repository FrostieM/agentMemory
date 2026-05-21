"""MCP handlers for /memory/review_queue + /memory/compact_trigger."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.config.settings import get_settings
from agent_memory_lite.maintenance.compact_trigger import check_compaction_threshold
from agent_memory_lite.maintenance.review_queue import build_review_queue
from agent_memory_lite.mcp.stdio_guards import _workspace_from_args
from agent_memory_lite.mcp.stdio_runtime import _runtime


def _handle_review_queue(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args, intent="read")
    return build_review_queue(
        _runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        limit_per_kind=int(args.get("limit_per_kind", 10)),
    )


def _handle_compact_trigger(args: dict[str, Any]) -> dict[str, Any]:
    # Round-5 audit: check_compaction_threshold WRITES a compaction_due
    # maintenance event when the workspace is overdue, so the probe needs
    # write intent — a strict project chat must not insert events into a
    # foreign workspace's DB.
    workspace_id = _workspace_from_args(args, intent="write")
    return check_compaction_threshold(
        _runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        settings=get_settings(),
    )
