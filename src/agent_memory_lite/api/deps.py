"""FastAPI dependency providers.

The embedding provider is cached one-per-process (model load is slow).
The DB connection is opened per-request and respects an optional
`X-Memory-DB-Path` request header so a single HTTP service can serve
multiple per-project memory files (used by the project-scoped
UserPromptSubmit hook). The vector store is cached unless the request
overrides `X-Memory-Vector-Path`, in which case a request-scoped store
is opened.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request

from agent_memory_lite.api.errors import ValidationError
from agent_memory_lite.config.settings import Settings, get_settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.embeddings.factory import get_embedding_provider
from agent_memory_lite.vector_store.base import VectorStore
from agent_memory_lite.vector_store.factory import get_vector_store
from agent_memory_lite.vector_store.lancedb_store import LanceDBStore
from agent_memory_lite.vector_store.sqlite_vec_store import SqliteVecStore

_embedding_singleton: EmbeddingProvider | None = None
_vector_store_singleton: VectorStore | None = None


def get_settings_dep() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


def ensure_workspace_readable(workspace_id: str, settings: Settings) -> None:
    """Reject reads only if `forbid_default_workspace` would be violated.

    Reads across registered workspaces are allowed in any chat: the user can
    explicitly ask the agent to look at another project's memory and the
    agent will route the read via per-call `X-Memory-DB-Path`. Strict
    isolation does NOT block reads — that asymmetry is intentional.
    """
    if settings.forbid_default_workspace and workspace_id == "default":
        raise ValidationError(
            "workspace_id='default' is disabled by MEMORY_FORBID_DEFAULT_WORKSPACE; "
            "pass the project workspace_id explicitly"
        )


def ensure_workspace_writable(workspace_id: str, settings: Settings) -> None:
    """Block writes to any workspace other than the strict anchor.

    Strict isolation (`MEMORY_STRICT_WORKSPACE_ISOLATION=true`) is a hard
    boundary on writes: a project chat can never write to another project's
    memory, even when the user asks. Reads are allowed (see
    `ensure_workspace_readable`) but writes are not — preventing a chat in
    one project from polluting another's audit log, decisions, or
    behavior instructions.

    Hub mode (`MEMORY_HUB_MODE=true`) opts out of strict isolation: the
    operator has chosen a shared hub service, so cross-workspace writes via
    `X-Memory-DB-Path` are explicit and allowed.
    """
    if settings.forbid_default_workspace and workspace_id == "default":
        raise ValidationError(
            "workspace_id='default' is disabled by MEMORY_FORBID_DEFAULT_WORKSPACE; "
            "pass the project workspace_id explicitly"
        )
    if (
        settings.strict_workspace_isolation
        and not settings.hub_mode
        and workspace_id != settings.workspace_id
    ):
        raise ValidationError(
            f"writes to workspace_id={workspace_id!r} are blocked by "
            f"MEMORY_STRICT_WORKSPACE_ISOLATION; expected {settings.workspace_id!r}. "
            "Reads are still allowed; writes require either the matching "
            "workspace anchor or hub mode."
        )


def ensure_workspace_allowed(workspace_id: str, settings: Settings) -> None:
    """Backwards-compatible alias for the write guard.

    Existing callers that don't distinguish read vs write fall back to the
    stricter of the two. New code should call `ensure_workspace_readable`
    or `ensure_workspace_writable` directly.
    """
    ensure_workspace_writable(workspace_id, settings)


def _header_or_query(request: Request, header_name: str, query_name: str) -> str | None:
    """Return a per-request override for path routing.

    Headers win because they are normal for HTTP clients (curl, httpx, the
    UserPromptSubmit hook). Query params exist so an `EventSource` SSE stream
    — which cannot attach custom headers from the browser — can still pin a
    specific physical DB.
    """
    value = request.headers.get(header_name)
    if value:
        return value
    return request.query_params.get(query_name)


def _resolve_db_path(request: Request, settings: Settings) -> Path:
    override = _header_or_query(request, "x-memory-db-path", "db_path")
    if override:
        return Path(override)
    return settings.db_path


def get_db_dep(request: Request, settings: SettingsDep) -> Iterator[sqlite3.Connection]:
    conn = open_connection(_resolve_db_path(request, settings))
    try:
        yield conn
    finally:
        close_connection(conn)


DbDep = Annotated[sqlite3.Connection, Depends(get_db_dep)]


def get_embedding_provider_dep(settings: SettingsDep) -> EmbeddingProvider:
    global _embedding_singleton  # noqa: PLW0603
    if _embedding_singleton is None:
        _embedding_singleton = get_embedding_provider(settings)
    return _embedding_singleton


EmbeddingProviderDep = Annotated[EmbeddingProvider, Depends(get_embedding_provider_dep)]


def _build_request_scoped_store(settings: Settings, override: str) -> VectorStore:
    if settings.vector_backend == "sqlite_vec":
        return SqliteVecStore(override)
    return LanceDBStore(override)


def get_vector_store_dep(request: Request, settings: SettingsDep) -> VectorStore:
    override = _header_or_query(request, "x-memory-vector-path", "vector_path")
    if override:
        return _build_request_scoped_store(settings, override)
    global _vector_store_singleton  # noqa: PLW0603
    if _vector_store_singleton is None:
        _vector_store_singleton = get_vector_store(settings)
    return _vector_store_singleton


VectorStoreDep = Annotated[VectorStore, Depends(get_vector_store_dep)]


def reset_dependency_singletons() -> None:
    """Test hook: drop cached provider + vector store so a new factory call runs."""
    global _embedding_singleton, _vector_store_singleton  # noqa: PLW0603
    _embedding_singleton = None
    _vector_store_singleton = None
