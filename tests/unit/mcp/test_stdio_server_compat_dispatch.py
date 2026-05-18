"""Unit tests for the v2 compat shim wiring inside stdio_server._call_tool.

Covers the *dispatch boundary*, not the translation logic itself
(``test_v2_compat.py`` already covers per-tool shape adapters):

* ``MEMORY_V2_COMPAT_ENABLED=false`` -> shim is a no-op, native v2
  handler runs.
* ``MEMORY_V2_COMPAT_ENABLED=true`` (default) -> legacy v2-only tool
  names are routed through the shim instead of the native v2 handler.
* Canonical names (``memory_search``, ``memory_write``, etc., served
  by MEMORY_HANDLERS) bypass the shim even when enabled.
* Shim internal raise -> returns None -> native handler still runs
  (failure-soft contract).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.mcp import stdio_server

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"


@pytest.fixture
def v3_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    """Fresh v3-schema DB injected as the runtime's working connection."""
    db_path = tmp_path / "canonical.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    monkeypatch.setattr(stdio_server._runtime, "db", lambda: conn)
    try:
        yield conn
    finally:
        conn.close()


# ============================================================
# _maybe_compat_dispatch behaviour
# ============================================================


def test_compat_off_returns_none(
    v3_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env flag explicitly ``false`` -> dispatch returns None so native runs."""
    monkeypatch.setenv("MEMORY_V2_COMPAT_ENABLED", "false")
    out = stdio_server._maybe_compat_dispatch(
        "memory_write_decision",
        {"workspace_id": "default", "title": "T", "decision_text": "B"},
    )
    assert out is None


def test_compat_on_routes_v2_name_through_shim(
    v3_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env flag on → v2 name returns an envelope from the shim."""
    monkeypatch.setenv("MEMORY_V2_COMPAT_ENABLED", "true")
    out = stdio_server._maybe_compat_dispatch(
        "memory_write_decision",
        {"workspace_id": "default", "title": "Hello", "decision_text": "World"},
    )
    assert out is not None
    assert out["ok"] is True
    assert "deprecation_notice" in out
    assert out["data"]["kind"] == "decision"


def test_compat_on_skips_v3_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """v3-prefixed names bypass the shim even when the env flag is on."""
    monkeypatch.setenv("MEMORY_V2_COMPAT_ENABLED", "true")
    out = stdio_server._maybe_compat_dispatch(
        "memory_search",
        {"workspace_id": "default", "query": "anything"},
    )
    assert out is None


def test_compat_on_unknown_v2_name_returns_none(
    v3_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v2 tool name with no shim entry falls through to the native handler."""
    monkeypatch.setenv("MEMORY_V2_COMPAT_ENABLED", "true")
    out = stdio_server._maybe_compat_dispatch(
        "memory_completely_unknown_tool", {"workspace_id": "default"}
    )
    assert out is None


def test_compat_dispatch_swallows_exceptions(
    v3_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the shim itself raises (e.g. DB conn broken), dispatch returns None
    rather than propagating the exception into the MCP framework."""
    monkeypatch.setenv("MEMORY_V2_COMPAT_ENABLED", "true")

    def boom(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("simulated shim crash")

    # stdio_server imported compat_dispatch into its namespace; patch the
    # bound reference, not the v2_compat module's.
    monkeypatch.setattr(stdio_server, "compat_dispatch", boom)
    out = stdio_server._maybe_compat_dispatch("memory_write_decision", {"workspace_id": "default"})
    assert out is None


# ============================================================
# Compat shim wiring is opt-in — verify no behavior change when off
# ============================================================


def test_compat_off_does_not_invoke_shim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirms the early-return path: compat_dispatch must not be called
    when MEMORY_V2_COMPAT_ENABLED is unset / falsy."""
    monkeypatch.delenv("MEMORY_V2_COMPAT_ENABLED", raising=False)
    invocations: list[tuple[str, dict[str, object]]] = []

    def tracker(_conn: object, name: str, args: dict[str, object]) -> None:
        invocations.append((name, args))

    monkeypatch.setattr(stdio_server, "compat_dispatch", tracker)
    stdio_server._maybe_compat_dispatch("memory_write_decision", {"x": 1})
    assert invocations == []
