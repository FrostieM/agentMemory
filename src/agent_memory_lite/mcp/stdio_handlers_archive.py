"""Handler for memory_archive: universal soft-delete dispatcher."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.ingestion.archive_service import archive_memory_object
from agent_memory_lite.mcp.stdio_guards import _with_workspace
from agent_memory_lite.mcp.stdio_http import _http_write
from agent_memory_lite.mcp.stdio_runtime import _runtime


def _handle_archive(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write("/memory/archive", payload, log_label="archive")
    if delegated is not None:
        return delegated

    archive_flag = payload.get("archive")
    archive = True if archive_flag is None else bool(archive_flag)
    return archive_memory_object(
        _runtime.db_for(str(payload["workspace_id"])),
        workspace_id=str(payload["workspace_id"]),
        kind=str(payload["kind"]),
        object_id=str(payload["id"]),
        archive=archive,
    )
