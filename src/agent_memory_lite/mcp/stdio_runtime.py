"""Per-process runtime for the MCP stdio server.

Owns the lazy SQLite connections, embedding provider, vector store, and
the path-resolution rules that decide which DB the server anchors to.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from pathlib import Path
from typing import Any

from agent_memory_lite.config.settings import Settings, get_settings
from agent_memory_lite.config.workspace_registry import WorkspaceRegistry
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.embeddings.factory import get_embedding_provider
from agent_memory_lite.mcp.stdio_env import _env_flag, _env_float, _memory_http_base_url
from agent_memory_lite.mcp.stdio_unresolved_warn import (
    _unresolved_warned,
    reset_unresolved_warned,
    warn_unresolved_once,
)
from agent_memory_lite.repositories.workspace_manifest_repo import ensure_workspace_manifest
from agent_memory_lite.vector_store.base import VectorStore
from agent_memory_lite.vector_store.factory import get_vector_store

__all__ = [
    "_Runtime",
    "_env_flag",
    "_env_float",
    "_memory_http_base_url",
    "_registry_paths_for",
    "_resolve_paths_from_cwd",
    "_runtime",
    # Re-exported from stdio_unresolved_warn so existing test imports
    # ``from agent_memory_lite.mcp.stdio_runtime import _unresolved_warned``
    # keep working after the SLOC-driven extraction.
    "_unresolved_warned",
    "reset_unresolved_warned",
    "warn_unresolved_once",
    "workspace_schema",
]


def _resolve_paths_from_cwd(settings: Settings) -> Settings:
    """Override settings.db_path / settings.vector_db_path from the cwd."""
    if os.environ.get("MEMORY_DB_PATH"):
        return settings
    cwd = Path.cwd()
    candidate_db = cwd / ".agent_memory" / "memory.db"
    candidate_vec = cwd / ".agent_memory" / "vectors.lance"
    try:
        registry = WorkspaceRegistry(settings.workspaces_file)
        entries = registry.list()
    except Exception:
        entries = []
    auto_hub = len(entries) > 1 and not settings.strict_workspace_isolation
    if candidate_db.parent.exists():
        update: dict[str, Any] = {"db_path": candidate_db, "vector_db_path": candidate_vec}
        if auto_hub:
            update["hub_mode"] = True
        return settings.model_copy(update=update)
    if len(entries) == 1:
        only = entries[0]
        return settings.model_copy(
            update={
                "db_path": Path(only.db_path),
                "vector_db_path": Path(only.vector_path),
                "workspace_id": only.id,
            }
        )
    if auto_hub:
        first = entries[0]
        return settings.model_copy(
            update={
                "db_path": Path(first.db_path),
                "vector_db_path": Path(first.vector_path),
                "workspace_id": first.id,
                "hub_mode": True,
            }
        )
    return settings


class _Runtime:
    """Lazy holders for the per-process SQLite + provider + store."""

    def __init__(self) -> None:
        self.settings = _resolve_paths_from_cwd(get_settings())
        self.conn: sqlite3.Connection | None = None
        self._conns_by_path: dict[str, sqlite3.Connection] = {}
        self._provider: EmbeddingProvider | None = None
        self._store: VectorStore | None = None

    def _open(self, db_path: Path | str, *, workspace_id: str) -> sqlite3.Connection:
        key = str(Path(db_path))
        cached = self._conns_by_path.get(key)
        if cached is not None:
            return cached
        conn = open_connection(Path(db_path))
        apply_migrations(conn)
        if self.settings.enforce_workspace_manifest:
            ensure_workspace_manifest(
                conn,
                workspace_id=workspace_id,
                allow_default_workspace=not self.settings.forbid_default_workspace,
            )
        self._conns_by_path[key] = conn
        return conn

    def db(self) -> sqlite3.Connection:
        if self.conn is None:
            self.conn = self._open(self.settings.db_path, workspace_id=self.settings.workspace_id)
        return self.conn

    def db_for(self, workspace_id: str | None) -> sqlite3.Connection:
        if not workspace_id:
            return self.db()
        resolved = _registry_paths_for(self, workspace_id)
        if resolved is None:
            warn_unresolved_once(
                workspace_id=workspace_id,
                anchor_id=self.settings.workspace_id,
                workspaces_file=self.settings.workspaces_file,
                anchor_db_path=self.settings.db_path,
            )
            return self.db()
        db_path, _ = resolved
        if Path(db_path) == Path(self.settings.db_path):
            return self.db()
        return self._open(Path(db_path), workspace_id=workspace_id)

    def provider(self) -> EmbeddingProvider:
        if self._provider is None:
            self._provider = get_embedding_provider(self.settings)
        return self._provider

    def store(self) -> VectorStore:
        if self._store is None:
            self._store = get_vector_store(self.settings)
        return self._store

    def close(self) -> None:
        if self.conn is not None:
            close_connection(self.conn)
            self.conn = None
        for conn in self._conns_by_path.values():
            with contextlib.suppress(Exception):
                close_connection(conn)
        self._conns_by_path.clear()
        if self._store is not None:
            self._store.close()
            self._store = None


def _registry_paths_for(runtime: _Runtime, workspace_id: str) -> tuple[str, str] | None:
    """Look up ``(db_path, vector_path)`` for ``workspace_id`` in the hub registry."""
    try:
        registry = WorkspaceRegistry(runtime.settings.workspaces_file)
        entry = registry.get(workspace_id)
    except Exception:
        return None
    if entry is None or not entry.db_path:
        return None
    return entry.db_path, entry.vector_path or str(runtime.settings.vector_db_path)


_runtime = _Runtime()


# 2.2 (Phase 2.7): workspace_schema() lives in stdio_workspace_schema.py
# so the resolution logic stays under the ≤150-SLOC ceiling. Re-imported
# here so every existing
# `from agent_memory_lite.mcp.stdio_runtime import workspace_schema`
# call site keeps working without churn. The name is listed in
# ``__all__`` above so this is a deliberate public re-export.
from agent_memory_lite.mcp.stdio_workspace_schema import workspace_schema  # noqa: E402
