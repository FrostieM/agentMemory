"""Handlers for decisions + task state."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.ingestion._write_helpers import (
    capability_suggestion_dicts,
    decision_neighbor_dicts,
    resolve_source_episode_id,
)
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.ingestion.task_state_writer import write_task_state
from agent_memory_lite.mcp.stdio_guards import _with_workspace, _workspace_from_args
from agent_memory_lite.mcp.stdio_http import _http_write
from agent_memory_lite.mcp.stdio_payloads import _decision_payload
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.task_state import TaskStateIn
from agent_memory_lite.repositories.decisions_repo import (
    list_active_decisions,
    list_all_decisions,
)


def _handle_write_decision(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write("/memory/write_decision", payload, log_label="write_decision")
    if delegated is not None:
        return delegated

    # Local-fallback path (HTTP service unreachable): mirror the HTTP
    # route — apply Move 1 (auto-thread source_episode_id) and Move 3
    # (capability_suggestions) so MCP-only deployments don't silently
    # lose either feature. ``allow_orphan`` is consumed by the helper
    # before DecisionIn() is built (the domain model has no such field).
    workspace_id = str(payload.get("workspace_id") or _runtime.settings.workspace_id)
    allow_orphan = bool(payload.pop("allow_orphan", False))
    conn = _runtime.db_for(workspace_id)
    payload["source_episode_id"] = resolve_source_episode_id(
        conn,
        workspace_id=workspace_id,
        explicit=payload.get("source_episode_id"),
        allow_orphan=allow_orphan,
        settings=_runtime.settings,
    )
    decision = write_decision(conn, DecisionIn(**payload))
    return {
        "decision_id": decision.id,
        "status": decision.status.value,
        "valid_from": decision.valid_from,
        "superseded_decision_id": decision.supersedes_decision_id,
        "source_episode_id": decision.source_episode_id,
        "capability_suggestions": capability_suggestion_dicts(
            conn,
            workspace_id=workspace_id,
            title=str(payload.get("title") or ""),
            text=str(payload.get("decision_text") or ""),
            rationale=payload.get("rationale"),
        ),
        "decision_neighbors": decision_neighbor_dicts(
            conn,
            workspace_id=workspace_id,
            title=str(payload.get("title") or ""),
            text=str(payload.get("decision_text") or ""),
            rationale=payload.get("rationale"),
            exclude_id=decision.id,
        ),
    }


def _handle_list_decisions(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args, intent="read")
    query = args.get("query")
    include_superseded = bool(args.get("include_superseded", False))
    limit = int(args.get("limit", 10))
    if include_superseded:
        decisions = list_all_decisions(
            _runtime.db_for(workspace_id),
            workspace_id,
            query=str(query) if query is not None else None,
            limit=limit,
        )
    else:
        decisions = list_active_decisions(
            _runtime.db_for(workspace_id),
            workspace_id,
            query=str(query) if query is not None else None,
            limit=limit,
        )
    return {"decisions": [_decision_payload(item) for item in decisions]}


def _handle_update_task_state(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write("/memory/update_task_state", payload, log_label="update_task_state")
    if delegated is not None:
        return delegated

    state = write_task_state(_runtime.db_for(str(payload["workspace_id"])), TaskStateIn(**payload))
    return {
        "state_id": state.id,
        "task_id": state.task_id,
        "status": state.status,
        "updated_at": state.updated_at,
    }


def _handle_record_with_evidence(args: dict[str, Any]) -> dict[str, Any]:
    """Move 2 (v2.2): bundle ingest_episode + write_decision + optional
    link_capability into one call. The MCP layer just delegates to the
    HTTP endpoint when one is reachable; the local-fallback path uses
    the same handler module as the HTTP route to keep behavior aligned.
    """
    payload = _with_workspace(args)
    delegated = _http_write(
        "/memory/record_with_evidence", payload, log_label="record_with_evidence"
    )
    if delegated is not None:
        return delegated
    # Local fallback (no HTTP service): import lazily so the handler
    # module stays importable when the optional ingestion deps aren't
    # configured. tools_compound.memory_record_with_evidence does the
    # full ingest + decision + link composition against the runtime
    # connection + provider/store.
    from agent_memory_lite.mcp.tools_compound import (  # noqa: PLC0415 — lazy import
        memory_record_with_evidence,
    )

    return memory_record_with_evidence(
        conn=_runtime.db_for(str(payload["workspace_id"])),
        embedding_provider=_runtime.provider(),
        vector_store=_runtime.store(),
        payload=payload,
    )
