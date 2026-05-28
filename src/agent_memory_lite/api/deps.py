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

import contextlib
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


def _allowed_db_paths(settings: Settings) -> set[Path]:
    """All physical DB paths the service is willing to honor via
    ``X-Memory-DB-Path`` override.

    v3.5 sector-4 audit-followup: previously the override accepted any
    string — a local process (or DNS-rebind attacker) could read any
    SQLite file on disk by setting the header. The allow-list is
    derived from (a) the anchor settings.db_path and (b) every
    workspace registered in the hub registry. Anything else is
    rejected as ``ValidationError`` BEFORE ``open_connection`` runs.
    """
    allowed: set[Path] = {settings.db_path.resolve()}
    try:
        from agent_memory_lite.config.workspace_registry import (  # noqa: PLC0415
            WorkspaceRegistry,
        )

        registry = WorkspaceRegistry(settings.workspaces_file)
        for entry in registry.list():
            try:
                allowed.add(Path(entry.db_path).resolve())
            except (OSError, ValueError):
                continue
    except Exception:
        pass
    return allowed


def _allowed_vector_paths(settings: Settings) -> set[Path]:
    allowed: set[Path] = {settings.vector_db_path.resolve()}
    try:
        from agent_memory_lite.config.workspace_registry import (  # noqa: PLC0415
            WorkspaceRegistry,
        )

        registry = WorkspaceRegistry(settings.workspaces_file)
        for entry in registry.list():
            try:
                if entry.vector_path:
                    allowed.add(Path(entry.vector_path).resolve())
            except (OSError, ValueError):
                continue
    except Exception:
        pass
    return allowed


def _registered_vector_path_for_db_path(settings: Settings, db_path: Path) -> Path | None:
    """Return the registered vector path that belongs to an explicit DB override."""
    if db_path == settings.db_path.resolve():
        return settings.vector_db_path.resolve()
    try:
        from agent_memory_lite.config.workspace_registry import (  # noqa: PLC0415
            WorkspaceRegistry,
        )

        registry = WorkspaceRegistry(settings.workspaces_file)
        for entry in registry.list():
            try:
                if Path(entry.db_path).resolve() == db_path:
                    vector_path = entry.vector_path or str(settings.vector_db_path)
                    return Path(vector_path).resolve()
            except (OSError, ValueError):
                continue
    except Exception:
        return None
    return None


def _resolve_db_path(request: Request, settings: Settings) -> Path:
    override = _header_or_query(request, "x-memory-db-path", "db_path")
    if not override:
        return settings.db_path
    # v3.5 sector-4 audit-followup: reject overrides that don't match
    # the registry. Path-traversal + reading /etc/passwd-style files
    # was previously trivially possible by simply setting the header.
    try:
        candidate = Path(override).resolve()
    except (OSError, ValueError) as exc:
        raise ValidationError(f"X-Memory-DB-Path is not a valid path: {exc}") from None
    allowed = _allowed_db_paths(settings)
    if candidate not in allowed:
        raise ValidationError(
            f"X-Memory-DB-Path {override!r} is not in the workspace registry. "
            "Register the workspace via /memory/workspaces or "
            "scripts/setup_agent.py before pointing the service at it."
        )
    return candidate


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


def _validated_vector_override(request: Request, settings: Settings, override: str) -> Path:
    db_override = _header_or_query(request, "x-memory-db-path", "db_path")
    # v3.5 sector-4 audit-followup: same allow-list as DB header.
    try:
        candidate = Path(override).resolve()
    except (OSError, ValueError) as exc:
        raise ValidationError(f"X-Memory-Vector-Path is not a valid path: {exc}") from None
    if candidate not in _allowed_vector_paths(settings):
        raise ValidationError(
            f"X-Memory-Vector-Path {override!r} is not in the workspace registry."
        )
    if db_override:
        try:
            db_candidate = Path(db_override).resolve()
        except (OSError, ValueError) as exc:
            raise ValidationError(f"X-Memory-DB-Path is not a valid path: {exc}") from None
        registered = _registered_vector_path_for_db_path(settings, db_candidate)
        if registered is not None and candidate != registered:
            raise ValidationError(
                "X-Memory-Vector-Path does not match the vector path registered "
                "for X-Memory-DB-Path."
            )
    else:
        anchor_vector_path = settings.vector_db_path.resolve()
        if candidate != anchor_vector_path:
            raise ValidationError(
                "X-Memory-Vector-Path does not match the active database. "
                "Pass the matching X-Memory-DB-Path header with a registered "
                "workspace vector path, or omit the vector override."
            )
    return candidate


def resolve_vector_store_for_request(
    request: Request,
    settings: Settings,
) -> tuple[VectorStore, bool]:
    """Resolve a vector store for a request.

    The boolean marks stores opened specifically for this request. Callers that
    bypass FastAPI's dependency generator must close owned stores after use.
    """
    override = _header_or_query(request, "x-memory-vector-path", "vector_path")
    if override:
        candidate = _validated_vector_override(request, settings, override)
        return _build_request_scoped_store(settings, str(candidate)), True
    db_override = _header_or_query(request, "x-memory-db-path", "db_path")
    if db_override:
        try:
            db_candidate = Path(db_override).resolve()
        except (OSError, ValueError) as exc:
            raise ValidationError(f"X-Memory-DB-Path is not a valid path: {exc}") from None
        derived = _registered_vector_path_for_db_path(settings, db_candidate)
        if derived is not None:
            if derived not in _allowed_vector_paths(settings):
                raise ValidationError(
                    f"X-Memory-Vector-Path {str(derived)!r} is not in the workspace registry."
                )
            return _build_request_scoped_store(settings, str(derived)), True
    global _vector_store_singleton  # noqa: PLW0603
    if _vector_store_singleton is None:
        _vector_store_singleton = get_vector_store(settings)
    return _vector_store_singleton, False


def get_vector_store_dep(request: Request, settings: SettingsDep) -> Iterator[VectorStore]:
    store, owned = resolve_vector_store_for_request(request, settings)
    try:
        yield store
    finally:
        if owned:
            with contextlib.suppress(Exception):
                store.close()


VectorStoreDep = Annotated[VectorStore, Depends(get_vector_store_dep)]


def reset_dependency_singletons() -> None:
    """Test hook: drop cached provider + vector store so a new factory call runs."""
    global _embedding_singleton, _vector_store_singleton  # noqa: PLW0603
    _embedding_singleton = None
    _vector_store_singleton = None
