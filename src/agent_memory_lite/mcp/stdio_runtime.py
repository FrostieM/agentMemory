"""Per-process runtime for the MCP stdio server.

Owns the lazy SQLite connections, embedding provider, vector store, and
the path-resolution rules that decide which DB the server anchors to.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from pathlib import Path

from agent_memory_lite.config.settings import get_settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.embeddings.factory import get_embedding_provider
from agent_memory_lite.mcp.stdio_env import _env_flag, _env_float, _memory_http_base_url
from agent_memory_lite.mcp.stdio_path_resolver import resolve_paths_from_cwd
from agent_memory_lite.mcp.stdio_runtime_provider import build_provider, is_remote_provider
from agent_memory_lite.mcp.stdio_runtime_registry import _registry_paths_for
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


# Backwards-compat alias: callers (tests, downstream code) historically
# imported ``_resolve_paths_from_cwd`` from this module. The function
# now lives in ``stdio_path_resolver`` to keep this file under the
# 150-SLOC ceiling.
_resolve_paths_from_cwd = resolve_paths_from_cwd


class _Runtime:
    """Lazy holders for the per-process SQLite + provider + store."""

    def __init__(self) -> None:
        self.settings = _resolve_paths_from_cwd(get_settings())
        self.conn: sqlite3.Connection | None = None
        self._conns_by_path: dict[str, sqlite3.Connection] = {}
        self._provider: EmbeddingProvider | None = None
        self._store: VectorStore | None = None
        self._stores_by_path: dict[str, VectorStore] = {}
        # Audit 1 #3: with N workspaces sharing one ``_Runtime`` (hub
        # mode), concurrent ``db_for`` calls can race on the cache. The
        # GIL makes single dict operations atomic, but the check-then-
        # open sequence is not — two threads can both miss the cache,
        # both open a connection, and both write. The second open leaks.
        # SQLite-level cross-thread is fine (open_connection sets
        # check_same_thread=False); this lock just guards the cache.
        self._open_lock = threading.Lock()
        # Guards the lazy single-instance provider()/store() init: the MCP
        # warm-up thread and a concurrent first handler must not each build one
        # (two provider instances would each load their own model).
        self._init_lock = threading.Lock()

    def _open(self, db_path: Path | str, *, workspace_id: str) -> sqlite3.Connection:
        key = str(Path(db_path))
        cached = self._conns_by_path.get(key)
        if cached is not None:
            return cached
        with self._open_lock:
            # Re-check inside the lock: another thread may have opened
            # the same path while we were waiting.
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
        # Resolve both sides (audit F3): store_for compares vector paths with
        # .resolve(); db_for MUST use the SAME normalization or the two routers
        # can disagree about whether a workspace is the anchor -- routing the SQL
        # row to a foreign DB while the episode vectors go to the anchor store
        # (or vice versa). Identical resolution keeps the pair consistent.
        if Path(db_path).resolve() == Path(self.settings.db_path).resolve():
            return self.db()
        return self._open(Path(db_path), workspace_id=workspace_id)

    def provider(self) -> EmbeddingProvider:
        if self._provider is None:
            with self._init_lock:
                if self._provider is None:
                    self._provider = self._build_provider()
        return self._provider

    def _build_provider(self) -> EmbeddingProvider:
        return build_provider(self.settings, get_embedding_provider)

    def using_remote_embeddings(self) -> bool:
        """True when provider() resolved to the out-of-process HTTP embedder, so
        stdio_server._run can skip the in-process warm-up and connect instantly."""
        return is_remote_provider(self.provider())

    def store(self) -> VectorStore:
        if self._store is None:
            with self._init_lock:
                if self._store is None:
                    self._store = get_vector_store(self.settings)
        return self._store

    def store_for(self, workspace_id: str | None) -> VectorStore:
        resolved = _registry_paths_for(self, workspace_id) if workspace_id else None
        anchor_vector_path = self.settings.vector_db_path.resolve()
        use_anchor_store = resolved is None
        path = anchor_vector_path
        if resolved is not None:
            db_path, vector_path = resolved
            use_anchor_store = Path(db_path).resolve() == self.settings.db_path.resolve()
            if not use_anchor_store:
                path = Path(vector_path).resolve()
                use_anchor_store = path == anchor_vector_path
        if use_anchor_store:
            return self.store()
        key = str(path)
        cached = self._stores_by_path.get(key)
        if cached is None:
            with self._open_lock:
                cached = self._stores_by_path.get(key)
                if cached is None:
                    settings = self.settings.model_copy(update={"vector_db_path": path})
                    cached = get_vector_store(settings)
                    self._stores_by_path[key] = cached
        return cached

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
        for store in self._stores_by_path.values():
            with contextlib.suppress(Exception):
                store.close()
        self._stores_by_path.clear()


_runtime = _Runtime()


# 2.2 (Phase 2.7): workspace_schema() lives in stdio_workspace_schema.py
# so the resolution logic stays under the ≤150-SLOC ceiling. Re-imported
# here so every existing
# `from agent_memory_lite.mcp.stdio_runtime import workspace_schema`
# call site keeps working without churn. The name is listed in
# ``__all__`` above so this is a deliberate public re-export.
from agent_memory_lite.mcp.stdio_workspace_schema import workspace_schema  # noqa: E402
