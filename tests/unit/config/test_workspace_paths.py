"""Unit tests for config.workspace_paths.

Covers ``connection_matches_workspace`` -- the shared predicate behind
the API write guard and the sentinel scheduler's pre-pass guard. The
resolver itself (``resolve_workspace_paths``) and the raising API
wrapper (``ensure_workspace_matches_db``) keep their coverage in
``tests/unit/api/test_workspace_routing.py``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from agent_memory_lite.config.workspace_paths import connection_matches_workspace
from agent_memory_lite.config.workspace_registry import WorkspaceRegistry


@dataclass
class _FakeSettings:
    """Stand-in for ``Settings`` — only the resolution-relevant fields."""

    workspace_id: str
    db_path: Path
    vector_db_path: Path
    workspaces_file: Path


def _settings(tmp_path: Path) -> _FakeSettings:
    return _FakeSettings(
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


def test_matches_false_on_definite_mismatch(tmp_path: Path) -> None:
    """Connection on the anchor DB but the workspace is registered elsewhere."""
    settings = _settings(tmp_path)
    _register(settings, "copyBot", tmp_path / "copybot.db")
    conn = _conn(settings.db_path)
    try:
        assert connection_matches_workspace(conn, "copyBot", settings) is False
    finally:
        conn.close()


def test_matches_true_on_correct_db(tmp_path: Path) -> None:
    """Connection on the workspace's own registered DB → match."""
    settings = _settings(tmp_path)
    copybot_db = tmp_path / "copybot.db"
    _register(settings, "copyBot", copybot_db)
    conn = _conn(copybot_db)
    try:
        assert connection_matches_workspace(conn, "copyBot", settings) is True
    finally:
        conn.close()


def test_matches_true_for_unregistered_workspace(tmp_path: Path) -> None:
    """No registry entry and not the anchor → nothing authoritative → skip."""
    settings = _settings(tmp_path)
    conn = _conn(settings.db_path)
    try:
        assert connection_matches_workspace(conn, "never-registered", settings) is True
    finally:
        conn.close()


def test_matches_true_for_in_memory_connection(tmp_path: Path) -> None:
    """An in-memory connection has no physical file to compare → skip."""
    settings = _settings(tmp_path)
    _register(settings, "copyBot", tmp_path / "copybot.db")
    conn = sqlite3.connect(":memory:")
    try:
        assert connection_matches_workspace(conn, "copyBot", settings) is True
    finally:
        conn.close()
