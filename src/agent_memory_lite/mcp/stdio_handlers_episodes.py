"""Handlers for the episodes / context / search / object surface."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.api.routes.context_post_build import apply_post_build_hooks
from agent_memory_lite.fts.query import search_chunks_fts
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.ingestion.file_pipeline import ingest_file
from agent_memory_lite.mcp.stdio_get_object import _local_get_object
from agent_memory_lite.mcp.stdio_guards import (
    _drop_none,
    _ensure_workspace_allowed,
    _with_workspace,
)
from agent_memory_lite.mcp.stdio_http import (
    _http_get_context,
    _http_ingest_episode,
    _http_ingest_file,
    _http_memory_request,
    _http_search,
)
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.retrieval.context_builder import build_context


def _handle_get_context(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="read")
    delegated = _http_get_context(payload)
    if delegated is not None:
        return delegated

    workspace_id = payload.get("workspace_id")
    query = RetrievalQuery(**payload)
    conn = _runtime.db_for(workspace_id)
    built = build_context(conn, query, embedding_provider=None, vector_store=None)
    # Same post-build hooks the HTTP route runs, so MCP-only deployments
    # without an HTTP service still fire v1.5 / v1.6 / v2.2 / v2.3.
    envelope_text = apply_post_build_hooks(
        conn,
        settings=_runtime.settings,
        request_workspace_id=str(workspace_id) if workspace_id else "",
        built=built,
        envelope_text=built.text,
    )
    return {
        "context_text": envelope_text,
        "sources": [
            {"id": hit.id, "score": hit.score, "sources": hit.sources, "path": hit.path}
            for hit in built.hits
        ],
    }


def _handle_get_object(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="read")
    delegated = _http_memory_request(
        path="/memory/get_object",
        payload=payload,
        enabled_env="MCP_GET_OBJECT_HTTP_DELEGATE",
        timeout_env="MCP_GET_OBJECT_HTTP_TIMEOUT_SEC",
        default_timeout=20.0,
        log_label="get_object",
    )
    if delegated is not None:
        return delegated

    workspace_id = str(payload["workspace_id"])
    kind = str(payload.get("kind", "")).strip()
    object_id = str(payload.get("id", "")).strip()
    include_evidence = bool(payload.get("include_evidence", False))
    if not kind or not object_id:
        raise ValueError("memory_get_object requires both 'kind' and 'id'")

    db = _runtime.db_for(workspace_id)
    found_obj = _local_get_object(db, kind, object_id, include_evidence=include_evidence)
    return {
        "kind": kind,
        "id": object_id,
        "workspace_id": workspace_id,
        "found": found_obj is not None,
        "object": found_obj,
    }


def _handle_search(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="read")
    payload.setdefault("mode", "fts")
    delegated = _http_search(payload)
    if delegated is not None:
        return delegated

    workspace_id = str(payload["workspace_id"])
    query = args["query"]
    limit = int(args.get("limit", 10))
    hits = search_chunks_fts(
        _runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        query=query,
        limit=limit,
    )
    return {
        "mode": "fts",
        "hits": [
            {
                "chunk_id": hit.chunk_id,
                "score": hit.score,
                "path": hit.path,
                "text": hit.text,
                "summary": hit.summary,
                "is_archived": hit.is_archived,
            }
            for hit in hits
        ],
    }


def _handle_ingest_episode(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    payload.setdefault("source_type", "agent_action")
    payload.setdefault("trust_level", "agent_observed")
    delegated = _http_ingest_episode(payload)
    if delegated is not None:
        return delegated

    result = ingest_episode(
        _runtime.db(),
        EpisodeIn(**payload),
        embedding_provider=_runtime.provider(),
        vector_store=_runtime.store(),
        auto_promote_settings=_runtime.settings,
    )
    return {
        "episode_id": result.episode.id,
        "chunk_id": result.chunk.id,
        "redacted_text": result.episode.raw_text,
        "redacted_kinds": result.redacted_kinds,
        "embedded": result.embedded,
        "auto_promoted_decisions": result.auto_promoted_decisions,
        "auto_promoted_rules": result.auto_promoted_rules,
        "auto_promoted_core": result.auto_promoted_core,
        "candidates_written": result.candidates_written,
    }


def _handle_ingest_file(args: dict[str, Any]) -> dict[str, Any]:
    payload = _drop_none(args)
    workspace_id = str(payload.pop("workspace_id", _runtime.settings.workspace_id))
    _ensure_workspace_allowed(workspace_id)
    payload["workspace_id"] = workspace_id
    delegated = _http_ingest_file(payload)
    if delegated is not None:
        return delegated

    result = ingest_file(
        _runtime.db(),
        workspace_id=workspace_id,
        embedding_provider=_runtime.provider(),
        vector_store=_runtime.store(),
        **payload,
    )
    return {
        "file_id": result.file.id,
        "path": result.file.path,
        "chunks_written": result.chunks_written,
        "skipped": result.skipped,
        "last_indexed_at": result.file.last_indexed_at,
    }
