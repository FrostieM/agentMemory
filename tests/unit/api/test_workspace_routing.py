"""Unit tests for the workspace_routing resolver + DB-match guard.

``ensure_workspace_matches_db`` rejects a write whose connection is not
the SQLite file the registry records for the target ``workspace_id``.
Regression coverage for the 2026-05-21 leak -- 134 copyBot
``ingest_file`` writes that silently landed in the agent-memory-lite
database because hub routing was bypassed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_memory_lite.api.errors import ValidationError
from agent_memory_lite.api.workspace_routing import (
    ensure_workspace_matches_db,
    resolve_workspace_paths,
)
from agent_memory_lite.config.workspace_registry import WorkspaceRegistry


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


def test_matches_db_noop_for_unregistered_workspace(tmp_path: Path) -> None:
    """An unregistered, non-anchor workspace has no authoritative path, so the
    guard skips rather than guess (``forbid_default_workspace`` covers the
    ``default`` workspace separately)."""
    settings = _settings(tmp_path)
    conn = _conn(settings.db_path)
    try:
        ensure_workspace_matches_db(conn, "never-registered", settings)  # no raise
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
