"""End-to-end registry routing for ``_runtime.db_for``.

The unit regression tests in ``test_memory_handlers.py`` patch
``_runtime.db_for`` itself to verify handlers call it with the right
arg. That locks the CALL SITE contract, but it does not validate that
the registry-loading machinery actually resolves a workspace id to the
right DB. This module fills the gap with a real, on-disk
``workspaces.json`` plus two real SQLite files — if any layer in the
chain regresses (registry parsing, path resolution, connection caching,
manifest enforcement), this test catches it.

Audit finding 2026-05-19 #5 (round-1 routing audit).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_memory_lite.mcp import stdio_guards, stdio_runtime
from agent_memory_lite.mcp import stdio_handlers_memory as v3_handlers
from agent_memory_lite.mcp.stdio_runtime import _Runtime, _runtime, reset_unresolved_warned
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS


def _seed_workspace_db(db_path: Path, *, workspace_id: str) -> None:
    """Initialise a real on-disk SQLite file with full migrations applied
    and a workspace_manifest row owned by ``workspace_id`` so the
    runtime's manifest guard accepts the file."""
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415
    from agent_memory_lite.repositories.workspace_manifest_repo import (  # noqa: PLC0415
        ensure_workspace_manifest,
    )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_connection(db_path)
    try:
        apply_migrations(conn)
        ensure_workspace_manifest(conn, workspace_id=workspace_id, allow_default_workspace=False)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def two_workspace_registry(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a ``workspaces.json`` registry with TWO entries, each backed
    by its own DB file. Returns (registry_path, alpha_db, beta_db).
    """
    alpha_db = tmp_path / "alpha" / "memory.db"
    beta_db = tmp_path / "beta" / "memory.db"
    alpha_vec = tmp_path / "alpha" / "vectors.lance"
    beta_vec = tmp_path / "beta" / "vectors.lance"
    _seed_workspace_db(alpha_db, workspace_id="alpha")
    _seed_workspace_db(beta_db, workspace_id="beta")
    registry_path = tmp_path / "workspaces.json"
    registry_path.write_text(
        json.dumps(
            {
                "version": 1,
                "workspaces": [
                    {
                        "id": "alpha",
                        "db_path": str(alpha_db),
                        "vector_path": str(alpha_vec),
                        "label": "alpha-test",
                    },
                    {
                        "id": "beta",
                        "db_path": str(beta_db),
                        "vector_path": str(beta_vec),
                        "label": "beta-test",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry_path, alpha_db, beta_db


def _make_runtime(registry_path: Path, anchor_db: Path) -> _Runtime:
    """Return a fresh ``_Runtime`` configured to use the test registry."""
    rt = _Runtime()
    rt.settings = rt.settings.model_copy(
        update={
            "workspaces_file": registry_path,
            "db_path": anchor_db,
            "workspace_id": "alpha",  # anchor matches one of the workspaces
            "enforce_workspace_manifest": True,
            "forbid_default_workspace": False,
            "strict_workspace_isolation": False,
            "hub_mode": True,
        }
    )
    return rt


def test_db_for_routes_explicit_workspace_via_registry(
    two_workspace_registry: tuple[Path, Path, Path],
) -> None:
    """Explicit workspace_id resolved via real workspaces.json → opens
    the registered DB, NOT the anchor."""
    registry_path, alpha_db, beta_db = two_workspace_registry
    rt = _make_runtime(registry_path, anchor_db=alpha_db)
    try:
        # Insert a marker row into beta DB so we can prove db_for hit it.
        beta_direct = sqlite3.connect(beta_db)
        beta_direct.execute(
            "INSERT INTO behaviors (id, workspace_id, name, kind, rule, "
            "confidence, importance, active, created_at, updated_at, pinned) "
            "VALUES ('beh_beta_marker', 'beta', 'beta-key', 'operating_rule', 'beta-value', "
            "0.9, 0.9, 1, datetime('now'), datetime('now'), 0)"
        )
        beta_direct.commit()
        beta_direct.close()

        conn = rt.db_for("beta")
        row = conn.execute(
            "SELECT rule FROM behaviors WHERE id = ?", ("beh_beta_marker",)
        ).fetchone()
        assert row is not None
        assert row[0] == "beta-value"
    finally:
        rt.close()


def test_db_for_falls_back_to_anchor_on_unknown_workspace(
    two_workspace_registry: tuple[Path, Path, Path],
) -> None:
    """Unknown workspace_id → fallback to anchor (alpha here). The warn
    function fires once per id but the routing still works.
    """
    registry_path, alpha_db, _ = two_workspace_registry
    rt = _make_runtime(registry_path, anchor_db=alpha_db)
    reset_unresolved_warned()
    try:
        # Drop a row into alpha (the anchor) so we can prove the
        # fallback connection is the anchor's.
        alpha_direct = sqlite3.connect(alpha_db)
        alpha_direct.execute(
            "INSERT INTO behaviors (id, workspace_id, name, kind, rule, "
            "confidence, importance, active, created_at, updated_at, pinned) "
            "VALUES ('beh_alpha_marker', 'alpha', 'alpha-key', 'operating_rule', 'alpha-value', "
            "0.9, 0.9, 1, datetime('now'), datetime('now'), 0)"
        )
        alpha_direct.commit()
        alpha_direct.close()

        conn = rt.db_for("nonexistent-workspace")
        row = conn.execute(
            "SELECT rule FROM behaviors WHERE id = ?", ("beh_alpha_marker",)
        ).fetchone()
        assert row is not None
        assert row[0] == "alpha-value"
        # Verified the warn-once side effect landed.
        assert "nonexistent-workspace" in stdio_runtime._unresolved_warned
    finally:
        rt.close()
        reset_unresolved_warned()


def test_db_for_caches_connection_per_path(
    two_workspace_registry: tuple[Path, Path, Path],
) -> None:
    """Two calls for the same workspace return the SAME connection
    object (cache by db_path key)."""
    registry_path, alpha_db, _ = two_workspace_registry
    rt = _make_runtime(registry_path, anchor_db=alpha_db)
    try:
        conn_a = rt.db_for("beta")
        conn_b = rt.db_for("beta")
        assert conn_a is conn_b
    finally:
        rt.close()


def test_store_for_routes_registered_workspace_vector_path(
    two_workspace_registry: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    fake_vector_store,
) -> None:
    registry_path, alpha_db, _ = two_workspace_registry
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    beta_vec = Path(registry["workspaces"][1]["vector_path"]).resolve()
    rt = _make_runtime(registry_path, anchor_db=alpha_db)
    built_paths: list[Path] = []

    def fake_get_vector_store(settings):
        built_paths.append(settings.vector_db_path)
        return fake_vector_store

    monkeypatch.setattr(stdio_runtime, "get_vector_store", fake_get_vector_store)

    try:
        store = rt.store_for("beta")
        assert store is fake_vector_store
        assert built_paths == [beta_vec]
        assert rt.store_for("beta") is store
        assert built_paths == [beta_vec]
    finally:
        rt.close()


def test_runtime_close_closes_registered_workspace_vector_stores(
    two_workspace_registry: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    fake_vector_store,
) -> None:
    registry_path, alpha_db, _ = two_workspace_registry
    rt = _make_runtime(registry_path, anchor_db=alpha_db)
    closed: list[object] = []

    class ClosableFakeStore(type(fake_vector_store)):
        def close(self) -> None:
            closed.append(self)

    store = ClosableFakeStore()
    monkeypatch.setattr(stdio_runtime, "get_vector_store", lambda _settings: store)

    rt.store_for("beta")
    rt.close()

    assert closed == [store]
    assert rt._stores_by_path == {}


def test_mcp_write_episode_routes_db_and_vector_via_registry(
    two_workspace_registry: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    registry_path, alpha_db, beta_db = two_workspace_registry
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    alpha_vec = Path(registry["workspaces"][0]["vector_path"]).resolve()
    beta_vec = Path(registry["workspaces"][1]["vector_path"]).resolve()
    rt = _make_runtime(registry_path, anchor_db=alpha_db)
    alpha_store = fake_vector_store
    beta_store = type(fake_vector_store)()
    stores = {alpha_vec: alpha_store, beta_vec: beta_store}
    built_paths: list[Path] = []

    def fake_get_vector_store(settings):
        path = settings.vector_db_path.resolve()
        built_paths.append(path)
        return stores[path]

    monkeypatch.setattr(stdio_runtime, "get_vector_store", fake_get_vector_store)
    monkeypatch.setattr(v3_handlers, "_runtime", rt)
    monkeypatch.setattr(stdio_guards, "_runtime", rt)
    monkeypatch.setattr(rt, "provider", lambda: fake_embedding_provider)
    reset_unresolved_warned()
    try:
        env = v3_handlers._handle_v3_write(
            {
                "workspace_id": "beta",
                "kind": "episode",
                "payload": {
                    "source_type": "agent_action",
                    "raw_text": "MCP write must route DB and vectors through registry",
                },
            }
        )
        assert env["ok"] is True, env
        assert env["data"]["embedded"] is True
        assert built_paths == [beta_vec]
        assert beta_store.count(NAMESPACE_CHUNKS, workspace_id="beta") == 1
        assert alpha_store.count(NAMESPACE_CHUNKS, workspace_id="beta") == 0

        beta_direct = sqlite3.connect(beta_db)
        beta_direct.row_factory = sqlite3.Row
        try:
            chunk = beta_direct.execute(
                "SELECT embedding_id FROM chunks WHERE id = ?",
                (env["data"]["chunk_id"],),
            ).fetchone()
            assert chunk is not None
            assert chunk["embedding_id"] == env["data"]["chunk_id"]
        finally:
            beta_direct.close()
    finally:
        rt.close()
        reset_unresolved_warned()


def test_mcp_store_for_anchor_db_uses_anchor_vector_even_if_registry_vector_differs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_vector_store,
) -> None:
    anchor_db = tmp_path / "anchor" / "memory.db"
    foreign_vec = tmp_path / "foreign" / "vectors.lance"
    _seed_workspace_db(anchor_db, workspace_id="alpha")
    registry_path = tmp_path / "workspaces.json"
    registry_path.write_text(
        json.dumps(
            {
                "version": 1,
                "workspaces": [
                    {
                        "id": "alias",
                        "db_path": str(anchor_db),
                        "vector_path": str(foreign_vec),
                        "label": "alias-to-anchor-db",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    rt = _make_runtime(registry_path, anchor_db=anchor_db)
    anchor_store = fake_vector_store
    built_paths: list[Path] = []

    def fake_get_vector_store(settings):
        built_paths.append(settings.vector_db_path.resolve())
        return anchor_store

    monkeypatch.setattr(stdio_runtime, "get_vector_store", fake_get_vector_store)

    try:
        store = rt.store_for("alias")
        assert store is anchor_store
        assert built_paths == [rt.settings.vector_db_path.resolve()]
    finally:
        rt.close()


def test_db_for_anchor_id_uses_anchor_connection(
    two_workspace_registry: tuple[Path, Path, Path],
) -> None:
    """Calling db_for with the anchor's own workspace_id (alpha) returns
    the anchor connection, not a fresh duplicate."""
    registry_path, alpha_db, _ = two_workspace_registry
    rt = _make_runtime(registry_path, anchor_db=alpha_db)
    try:
        anchor_conn = rt.db()
        routed_conn = rt.db_for("alpha")
        assert anchor_conn is routed_conn
    finally:
        rt.close()


def test_concurrent_db_for_opens_connection_exactly_once(
    two_workspace_registry: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit 1 #3 regression — strengthened per audit-round-2 finding A#1.

    The previous version of this test just checked all 20 threads got the
    same cached connection back. That assertion passes even WITHOUT the
    lock as long as the first thread finishes ``open_connection`` before
    any other thread reaches the cache-check — which on a fast laptop
    happens nearly always. To actually exercise the race window, this
    revision:

    1. Wraps ``open_connection`` in a counter + adds a brief sleep so
       the race window stays wide enough for 20 threads to overlap.
    2. Asserts ``open_connection`` was called EXACTLY ONCE (across the
       barrier-released cohort). Without the lock the count would be 2+.
    3. Still verifies all 20 threads observe the same cached object.
    """
    import threading  # noqa: PLC0415
    import time  # noqa: PLC0415

    from agent_memory_lite.mcp import stdio_runtime as runtime_mod  # noqa: PLC0415

    registry_path, alpha_db, _ = two_workspace_registry
    rt = _make_runtime(registry_path, anchor_db=alpha_db)
    open_calls = [0]
    open_calls_lock = threading.Lock()
    real_open_connection = runtime_mod.open_connection

    def counted_open(*args: object, **kwargs: object) -> sqlite3.Connection:
        with open_calls_lock:
            open_calls[0] += 1
        # Hold the lock-protected critical section open long enough that
        # other threads have a fair chance to race past the outside-lock
        # check. 50ms is plenty.
        time.sleep(0.05)
        return real_open_connection(*args, **kwargs)

    monkeypatch.setattr(runtime_mod, "open_connection", counted_open)

    try:
        results: list[sqlite3.Connection] = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(20)

        def grab() -> None:
            barrier.wait()
            conn = rt.db_for("beta")
            with results_lock:
                results.append(conn)

        workers = [threading.Thread(target=grab) for _ in range(20)]
        for w in workers:
            w.start()
        for w in workers:
            w.join(timeout=10)

        assert len(results) == 20
        unique = {id(c) for c in results}
        assert len(unique) == 1
        # The lock-protected invariant: open_connection runs EXACTLY ONCE
        # for the 20-thread barrier-released cohort. If the lock were
        # removed, multiple threads would see cache=None concurrently
        # and each call open_connection.
        assert open_calls[0] == 1, (
            f"open_connection called {open_calls[0]} times; expected 1 — "
            "the per-runtime open lock is not actually serializing opens."
        )
    finally:
        rt.close()


def test_runtime_singleton_db_for_routes_correctly(
    two_workspace_registry: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Module-level ``_runtime`` (the production singleton) routes the
    same way when patched to use the test registry. Validates that
    handler code paths — which import and use ``_runtime`` — get the
    same routing semantics as a freshly-constructed ``_Runtime``.
    """
    registry_path, alpha_db, _beta_db = two_workspace_registry
    relaxed = _runtime.settings.model_copy(
        update={
            "workspaces_file": registry_path,
            "db_path": alpha_db,
            "workspace_id": "alpha",
            "enforce_workspace_manifest": True,
            "forbid_default_workspace": False,
            "strict_workspace_isolation": False,
            "hub_mode": True,
        }
    )
    monkeypatch.setattr(_runtime, "settings", relaxed)
    # Drop existing cached connections so the new settings take effect.
    _runtime.close()
    reset_unresolved_warned()
    try:
        beta_conn = _runtime.db_for("beta")
        beta_conn.execute(
            "INSERT INTO behaviors (id, workspace_id, name, kind, rule, "
            "confidence, importance, active, created_at, updated_at, pinned) "
            "VALUES ('beh_singleton_marker', 'beta', 'k', 'operating_rule', 'v', "
            "0.9, 0.9, 1, datetime('now'), datetime('now'), 0)"
        )
        beta_conn.commit()
        # Re-fetch via the singleton and verify the row is visible.
        again = _runtime.db_for("beta")
        row = again.execute(
            "SELECT rule FROM behaviors WHERE id = ?", ("beh_singleton_marker",)
        ).fetchone()
        assert row is not None
        assert row[0] == "v"
        # And alpha (anchor) does NOT see beta's marker.
        alpha_conn = _runtime.db_for("alpha")
        alpha_row = alpha_conn.execute(
            "SELECT rule FROM behaviors WHERE id = ?", ("beh_singleton_marker",)
        ).fetchone()
        assert alpha_row is None
    finally:
        _runtime.close()
        reset_unresolved_warned()
