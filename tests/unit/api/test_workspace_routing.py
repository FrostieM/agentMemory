"""Unit tests for the workspace_routing resolver + DB-match guard.

``ensure_workspace_matches_db`` rejects a write whose connection is not
the SQLite file the registry records for the target ``workspace_id``.
Regression coverage for the 2026-05-21 leak -- 134 copyBot
``ingest_file`` writes that silently landed in the agent-memory-lite
database because hub routing was bypassed.
"""

from __future__ import annotations

import ast
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_memory_lite.api.errors import ValidationError
from agent_memory_lite.api.workspace_routing import (
    ensure_store_matches_workspace,
    ensure_workspace_matches_db,
    resolve_workspace_paths,
)
from agent_memory_lite.config.workspace_registry import WorkspaceRegistry
from agent_memory_lite.vector_store.lancedb_store import LanceDBStore


@dataclass
class _FakeSettings:
    """Stand-in for ``Settings`` — only the routing-relevant fields."""

    hub_mode: bool
    workspace_id: str
    db_path: Path
    vector_db_path: Path
    workspaces_file: Path


def _settings(tmp_path: Path, *, hub_mode: bool = True) -> _FakeSettings:
    return _FakeSettings(
        hub_mode=hub_mode,
        workspace_id="anchor-ws",
        db_path=tmp_path / "anchor.db",
        vector_db_path=tmp_path / "anchor.lance",
        workspaces_file=tmp_path / "workspaces.json",
    )


def _register(settings: _FakeSettings, workspace_id: str, db_path: Path) -> None:
    WorkspaceRegistry(settings.workspaces_file).register(
        workspace_id=workspace_id,
        db_path=str(db_path),
        vector_path=str(db_path.with_suffix(".lance")),
    )


def _conn(path: Path) -> sqlite3.Connection:
    """A real file-backed connection so ``PRAGMA database_list`` has a path."""
    return sqlite3.connect(str(path))


def test_http_routes_with_db_writes_also_check_physical_db() -> None:
    """Every route-level write guard must pair with the physical DB guard.

    Exceptions:
    * ui_brain_actions centralizes both checks in _ensure_write_routed.
    * evals writes into a temporary in-memory evaluation DB, not DbDep.
    """
    routes_dir = Path(__file__).parents[3] / "src" / "agent_memory_lite" / "api" / "routes"
    missing: list[tuple[str, str, int]] = []
    for path in sorted(routes_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            calls: set[str] = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name):
                        calls.add(child.func.id)
                    elif isinstance(child.func, ast.Attribute):
                        calls.add(child.func.attr)
            if "ensure_workspace_writable" not in calls:
                continue
            if path.name == "ui_brain_actions.py" and node.name == "_ensure_write_routed":
                continue
            if path.name == "evals.py" and node.name == "run_evals_route":
                continue
            if "ensure_workspace_matches_db" not in calls:
                missing.append((path.name, node.name, node.lineno))

    assert missing == []


def test_matches_db_rejects_leak_even_with_hub_mode_off(tmp_path: Path) -> None:
    """Round-1 audit §4: with hub_mode AND strict isolation off,
    ``ensure_workspace_writable`` permits a foreign write — so this guard
    must run in every mode, not just hub mode, or that config leaks."""
    settings = _settings(tmp_path, hub_mode=False)
    _register(settings, "copyBot", tmp_path / "copybot.db")
    conn = _conn(settings.db_path)  # anchor DB — the wrong file for copyBot
    try:
        with pytest.raises(ValidationError, match="routed to the wrong database"):
            ensure_workspace_matches_db(conn, "copyBot", settings)
    finally:
        conn.close()


def test_matches_db_rejects_foreign_workspace_on_anchor_db(tmp_path: Path) -> None:
    """The 2026-05-21 leak: workspace_id=copyBot, connection on the anchor DB."""
    settings = _settings(tmp_path)
    _register(settings, "copyBot", tmp_path / "copybot.db")
    conn = _conn(settings.db_path)  # anchor DB — the WRONG file for copyBot
    try:
        with pytest.raises(ValidationError, match="routed to the wrong database"):
            ensure_workspace_matches_db(conn, "copyBot", settings)
    finally:
        conn.close()


def test_matches_db_allows_foreign_workspace_on_its_own_db(tmp_path: Path) -> None:
    """Correctly routed: a copyBot write on copyBot's registered DB passes."""
    settings = _settings(tmp_path)
    copybot_db = tmp_path / "copybot.db"
    _register(settings, "copyBot", copybot_db)
    conn = _conn(copybot_db)
    try:
        ensure_workspace_matches_db(conn, "copyBot", settings)  # must not raise
    finally:
        conn.close()


def test_matches_db_allows_anchor_workspace_on_anchor_db(tmp_path: Path) -> None:
    """The service's own workspace writing to its own DB is always fine."""
    settings = _settings(tmp_path)
    conn = _conn(settings.db_path)
    try:
        ensure_workspace_matches_db(conn, "anchor-ws", settings)  # must not raise
    finally:
        conn.close()


def test_matches_db_rejects_unregistered_workspace_on_physical_db(tmp_path: Path) -> None:
    """Unregistered non-anchor writes must not fall through into the anchor DB."""
    settings = _settings(tmp_path)
    conn = _conn(settings.db_path)
    try:
        with pytest.raises(ValidationError, match="is not registered"):
            ensure_workspace_matches_db(conn, "never-registered", settings)
    finally:
        conn.close()


def test_matches_db_noop_for_in_memory_connection(tmp_path: Path) -> None:
    """An in-memory connection has no physical file to compare — skip."""
    settings = _settings(tmp_path)
    _register(settings, "copyBot", tmp_path / "copybot.db")
    conn = sqlite3.connect(":memory:")
    try:
        ensure_workspace_matches_db(conn, "copyBot", settings)  # must not raise
    finally:
        conn.close()


def test_matches_db_rejects_leak_despite_path_format_skew(tmp_path: Path) -> None:
    """Round-1 audit §1: a registry db_path written in a different but
    equivalent form (a redundant ``nested/..`` detour) does not let the
    leak slip past — the guard resolves both paths before comparing."""
    settings = _settings(tmp_path)
    (tmp_path / "nested").mkdir()
    _register(settings, "copyBot", tmp_path / "nested" / ".." / "copybot.db")
    conn = _conn(settings.db_path)  # anchor DB
    try:
        with pytest.raises(ValidationError, match="routed to the wrong database"):
            ensure_workspace_matches_db(conn, "copyBot", settings)
    finally:
        conn.close()


def test_matches_db_allows_routed_write_despite_path_format_skew(tmp_path: Path) -> None:
    """Round-1 audit §2: when the connection IS the workspace's DB, a
    string-form difference between the registry path and the connection
    path must not produce a false rejection."""
    settings = _settings(tmp_path)
    (tmp_path / "nested").mkdir()
    copybot_db = tmp_path / "copybot.db"
    _register(settings, "copyBot", tmp_path / "nested" / ".." / "copybot.db")
    conn = _conn(copybot_db)
    try:
        ensure_workspace_matches_db(conn, "copyBot", settings)  # must not raise
    finally:
        conn.close()


def test_matches_db_skips_on_unreadable_connection(tmp_path: Path) -> None:
    """A closed connection yields no path — the guard skips rather than
    crash the request (``_connection_db_path`` swallows sqlite3.Error)."""
    settings = _settings(tmp_path)
    _register(settings, "copyBot", tmp_path / "copybot.db")
    conn = _conn(settings.db_path)
    conn.close()
    ensure_workspace_matches_db(conn, "copyBot", settings)  # must not raise


def test_resolve_workspace_paths_maps_registered_foreign_workspace(tmp_path: Path) -> None:
    """A registered non-anchor workspace resolves to its own registered pair."""
    settings = _settings(tmp_path)
    copybot_db = tmp_path / "copybot.db"
    _register(settings, "copyBot", copybot_db)
    resolved = resolve_workspace_paths("copyBot", settings)
    assert resolved is not None
    assert Path(resolved.db_path) == copybot_db


def test_resolve_workspace_paths_none_for_anchor_workspace(tmp_path: Path) -> None:
    """The anchor workspace needs no routing — the resolver returns None."""
    settings = _settings(tmp_path)
    _register(settings, "anchor-ws", settings.db_path)
    assert resolve_workspace_paths("anchor-ws", settings) is None


def test_matches_db_noop_when_registry_colocates_workspace_in_anchor(tmp_path: Path) -> None:
    """Round-2 audit §1: when the registry records a workspace's db_path
    as the anchor DB itself, the registry says the workspace is co-located
    there — a write landing on the anchor is registry-consistent, so the
    guard skips. Registry corruption (a workspace pointed at the wrong DB)
    is memory_audit's domain, not this routing guard's."""
    settings = _settings(tmp_path)
    _register(settings, "copyBot", settings.db_path)  # registry: copyBot in the anchor
    conn = _conn(settings.db_path)
    try:
        ensure_workspace_matches_db(conn, "copyBot", settings)  # must not raise
    finally:
        conn.close()


def test_matches_db_rejects_write_to_an_unrelated_third_db(tmp_path: Path) -> None:
    """Round-2 audit §5: the guard rejects a misroute to ANY wrong DB, not
    only to the anchor — here the connection landed on a third file that is
    neither the anchor nor copyBot's registered DB."""
    settings = _settings(tmp_path)
    _register(settings, "copyBot", tmp_path / "copybot.db")
    conn = _conn(tmp_path / "third.db")  # neither anchor nor copyBot's DB
    try:
        with pytest.raises(ValidationError, match="routed to the wrong database"):
            ensure_workspace_matches_db(conn, "copyBot", settings)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Vector-store backstop (audit F3): the LanceDB analogue of the SQL guard.
# Episode vectors route on a path SEPARATE from the SQL conn, so they need
# their own physical check or vectors can leak cross-workspace while the SQL
# row is correct.
# ---------------------------------------------------------------------------


class _FakeStore:
    """Stand-in for a vector store -- only the ``_db_path`` LanceDBStore exposes."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path


def test_store_matches_allows_anchor_on_anchor_lance(tmp_path: Path) -> None:
    """The anchor workspace embedding into its own .lance is always fine."""
    settings = _settings(tmp_path)
    store = _FakeStore(settings.vector_db_path)
    ensure_store_matches_workspace(store, "anchor-ws", settings)  # must not raise


def test_store_matches_allows_foreign_on_its_own_lance(tmp_path: Path) -> None:
    """Correctly routed: copyBot vectors on copyBot's registered .lance pass."""
    settings = _settings(tmp_path)
    copybot_db = tmp_path / "copybot.db"
    _register(settings, "copyBot", copybot_db)
    store = _FakeStore(copybot_db.with_suffix(".lance"))
    ensure_store_matches_workspace(store, "copyBot", settings)  # must not raise


def test_store_matches_rejects_foreign_vectors_on_anchor_lance(tmp_path: Path) -> None:
    """Vector-side 2026-05-21 analogue: copyBot episode vectors heading into the
    anchor's .lance while copyBot has its own registered store -- rejected."""
    settings = _settings(tmp_path)
    _register(settings, "copyBot", tmp_path / "copybot.db")
    store = _FakeStore(settings.vector_db_path)  # anchor .lance -- WRONG for copyBot
    with pytest.raises(ValidationError, match="wrong vector store"):
        ensure_store_matches_workspace(store, "copyBot", settings)


def test_store_matches_rejects_unregistered_workspace(tmp_path: Path) -> None:
    """An unregistered non-anchor workspace has no authoritative .lance -- the
    guard fails closed rather than guess a store for it."""
    settings = _settings(tmp_path)
    store = _FakeStore(settings.vector_db_path)
    with pytest.raises(ValidationError, match="is not registered"):
        ensure_store_matches_workspace(store, "never-registered", settings)


def test_store_matches_noop_for_store_without_path(tmp_path: Path) -> None:
    """A store exposing no physical path (in-memory / test double) is skipped."""
    settings = _settings(tmp_path)
    _register(settings, "copyBot", tmp_path / "copybot.db")
    ensure_store_matches_workspace(object(), "copyBot", settings)  # must not raise


def test_store_matches_reads_real_lancedb_store_path_attribute(tmp_path: Path) -> None:
    """Pin the attribute the guard reads against the REAL store class.

    ``ensure_store_matches_workspace`` keys on ``LanceDBStore._db_path``. If that
    attribute is ever renamed the guard would silently no-op (``getattr`` -> None)
    AND the ``_FakeStore``-based tests above -- which hardcode the same name --
    would keep passing, regressing the protection invisibly. Driving a real
    ``LanceDBStore`` (constructed only, no ``.open()`` so no lancedb dependency)
    through the guard fails loudly on such a rename.
    """
    settings = _settings(tmp_path)
    _register(settings, "copyBot", tmp_path / "copybot.db")
    store = LanceDBStore(settings.vector_db_path)  # anchor .lance -- WRONG for copyBot
    with pytest.raises(ValidationError, match="wrong vector store"):
        ensure_store_matches_workspace(store, "copyBot", settings)
