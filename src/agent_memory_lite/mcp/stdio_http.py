"""HTTP delegation helpers used by the MCP handlers.

The stdio MCP process is frequently short-lived. Loading the embedding
model inside that process can block tool calls for minutes, while the
local HTTP service already owns a warmed provider/vector store. These
helpers POST the call to the HTTP service and let the in-process
fallback take over only when HTTP is unavailable.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.mcp.stdio_runtime import (
    _env_flag,
    _env_float,
    _memory_http_base_url,
    _registry_paths_for,
    _runtime,
)

_log = get_logger("mcp.stdio_server")
_HTTP_GET_CONTEXT_DEFAULT_TIMEOUT_SECONDS = 2.0
_HTTP_SEARCH_DEFAULT_TIMEOUT_SECONDS = 5.0
_HTTP_WRITE_DEFAULT_TIMEOUT_SECONDS = 30.0


def _http_memory_request(
    *,
    path: str,
    payload: dict[str, Any],
    enabled_env: str,
    timeout_env: str,
    default_timeout: float,
    log_label: str,
) -> dict[str, Any] | None:
    """Delegate a memory call to the warm local HTTP service when possible."""
    if not _env_flag(enabled_env, default=True):
        return None

    base_url = _memory_http_base_url(_runtime.settings)
    timeout = _env_float(timeout_env, default=default_timeout)
    data = json.dumps(payload).encode("utf-8")

    workspace_id = str(payload.get("workspace_id") or _runtime.settings.workspace_id)
    db_path = str(_runtime.settings.db_path)
    vector_path = str(_runtime.settings.vector_db_path)
    resolved = _registry_paths_for(_runtime, workspace_id)
    if resolved is not None:
        db_path, vector_path = resolved

    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Memory-DB-Path": db_path,
            "X-Memory-Vector-Path": vector_path,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_body: Any = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
        _log.warning("MCP %s HTTP delegation failed; using local fallback: %s", log_label, exc)
        return None

    if not isinstance(raw_body, dict):
        _log.warning("MCP %s HTTP delegation returned an unexpected payload", log_label)
        return None
    return raw_body


def _http_get_context(payload: dict[str, Any]) -> dict[str, Any] | None:
    body = _http_memory_request(
        path="/memory/get_context",
        payload=payload,
        enabled_env="MCP_GET_CONTEXT_HTTP_DELEGATE",
        timeout_env="MCP_GET_CONTEXT_HTTP_TIMEOUT_SEC",
        default_timeout=_HTTP_GET_CONTEXT_DEFAULT_TIMEOUT_SECONDS,
        log_label="get_context",
    )
    if body is None:
        return None
    if not isinstance(body.get("context_text"), str):
        _log.warning("MCP get_context HTTP delegation returned an unexpected payload")
        return None
    sources = body.get("sources")
    if not isinstance(sources, list):
        body["sources"] = []
    return body


def _http_search(payload: dict[str, Any]) -> dict[str, Any] | None:
    body = _http_memory_request(
        path="/memory/search",
        payload={**payload, "mode": payload.get("mode", "fts")},
        enabled_env="MCP_SEARCH_HTTP_DELEGATE",
        timeout_env="MCP_SEARCH_HTTP_TIMEOUT_SEC",
        default_timeout=_HTTP_SEARCH_DEFAULT_TIMEOUT_SECONDS,
        log_label="search",
    )
    if body is not None and isinstance(body.get("hits"), list):
        return body
    return None


def _http_ingest_episode(payload: dict[str, Any]) -> dict[str, Any] | None:
    body = _http_memory_request(
        path="/memory/ingest_episode",
        payload=payload,
        enabled_env="MCP_INGEST_HTTP_DELEGATE",
        timeout_env="MCP_INGEST_HTTP_TIMEOUT_SEC",
        default_timeout=_HTTP_WRITE_DEFAULT_TIMEOUT_SECONDS,
        log_label="ingest_episode",
    )
    if body is not None and {"episode_id", "chunk_id"}.issubset(body):
        return body
    return None


def _http_ingest_file(payload: dict[str, Any]) -> dict[str, Any] | None:
    body = _http_memory_request(
        path="/memory/ingest_file",
        payload=payload,
        enabled_env="MCP_INGEST_HTTP_DELEGATE",
        timeout_env="MCP_INGEST_HTTP_TIMEOUT_SEC",
        default_timeout=_HTTP_WRITE_DEFAULT_TIMEOUT_SECONDS,
        log_label="ingest_file",
    )
    if body is not None and {"file_id", "path"}.issubset(body):
        return body
    return None


def _http_write(path: str, payload: dict[str, Any], *, log_label: str) -> dict[str, Any] | None:
    return _http_memory_request(
        path=path,
        payload=payload,
        enabled_env="MCP_WRITE_HTTP_DELEGATE",
        timeout_env="MCP_WRITE_HTTP_TIMEOUT_SEC",
        default_timeout=_HTTP_WRITE_DEFAULT_TIMEOUT_SECONDS,
        log_label=log_label,
    )


def _http_read(path: str, payload: dict[str, Any], *, log_label: str) -> dict[str, Any] | None:
    """1.4.0: thin helper for read-side HTTP delegation. Used by
    `_handle_find_symbols` and any future read endpoint that wants to
    benefit from the warm HTTP service without re-stating the full
    `_http_memory_request` argument list every call site.
    """
    return _http_memory_request(
        path=path,
        payload=payload,
        enabled_env="MCP_SEARCH_HTTP_DELEGATE",
        timeout_env="MCP_SEARCH_HTTP_TIMEOUT_SEC",
        default_timeout=_HTTP_SEARCH_DEFAULT_TIMEOUT_SECONDS,
        log_label=log_label,
    )
