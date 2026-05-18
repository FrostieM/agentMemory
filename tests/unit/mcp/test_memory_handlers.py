"""Unit tests for v3 MCP handlers.

Each handler is called directly with a stubbed ``_runtime.db()`` that
points at an isolated v3-schema SQLite database. Envelope shape
matches what the HTTP routes return — verified by inspecting the
top-level ``{ok, data, error}`` keys.

The MCP dispatcher is exercised in the broader stdio_server test;
here we focus on the handler bodies.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.mcp import stdio_handlers_memory as v3
from agent_memory_lite.mcp.stdio_handlers import _HANDLERS
from agent_memory_lite.mcp.stdio_tools import ALL_TOOLS

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"


@pytest.fixture
def db_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    """Fresh v3-schema DB. Swaps `_runtime.db()` to return this conn.

    Also swaps `_runtime.settings` for a copy with hub_mode=True and
    strict_workspace_isolation=False so the v3 write guard does not
    block writes to the test workspace ("default").
    """
    db_path = tmp_path / "canonical.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    # Patch the runtime db accessor used by every v3 handler.
    monkeypatch.setattr(v3._runtime, "db", lambda: conn)
    # Settings is a frozen pydantic model — replace the whole instance.
    relaxed = v3._runtime.settings.model_copy(
        update={
            "workspace_id": "default",
            "strict_workspace_isolation": False,
            "forbid_default_workspace": False,
            "hub_mode": True,
        }
    )
    monkeypatch.setattr(v3._runtime, "settings", relaxed)
    try:
        yield conn
    finally:
        conn.close()


def _seed_decision(conn: sqlite3.Connection, **kwargs: object) -> str:
    payload = {"title": "T", "decision_text": "Body of the decision."}
    payload.update(kwargs)
    env = v3._handle_v3_write({"workspace_id": "default", "kind": "decision", "payload": payload})
    assert env["ok"] is True, env
    assert isinstance(env["data"], dict)
    return str(env["data"]["id"])


# ============================================================
# Registration: every v3 tool has a matching handler
# ============================================================


def test_v3_tools_match_handlers() -> None:
    # Canonical v3 surface: names are version-free post 2026-05-18 rename.
    canonical = {
        "memory_search",
        "memory_get",
        "memory_write",
        "memory_edit",
        "memory_pin",
        "memory_archive",
        "memory_brief",
        "memory_lint",
        "memory_invoke_skill",
        "memory_impact_check",
    }
    tool_names = {t.name for t in ALL_TOOLS} & canonical
    handler_names = set(_HANDLERS) & canonical
    assert tool_names == canonical, f"missing tools: {canonical - tool_names}"
    assert handler_names == canonical, f"missing handlers: {canonical - handler_names}"
    # 6 strict + 2 hook + invoke_skill + impact_check (discipline primitive)
    assert len(canonical) == 10


# ============================================================
# Envelope shape — uniform across handlers
# ============================================================


def test_envelope_shape_on_success(db_conn: sqlite3.Connection) -> None:
    dec_id = _seed_decision(db_conn)
    env = v3._handle_v3_get({"workspace_id": "default", "kind": "decision", "id": dec_id})
    assert set(env.keys()) == {"ok", "data", "error"}
    assert env["ok"] is True
    assert env["error"] is None
    assert isinstance(env["data"], dict)


def test_envelope_shape_on_error(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_get({"workspace_id": "default", "kind": "decision", "id": "missing"})
    assert env["ok"] is False
    assert env["data"] is None
    assert env["error"]["code"] == "not_found"


# ============================================================
# Read handlers
# ============================================================


def test_search_returns_projections(db_conn: sqlite3.Connection) -> None:
    _seed_decision(db_conn, title="Kelly sizing", decision_text="Use quarter-Kelly")
    _seed_decision(db_conn, title="Unrelated", decision_text="Other")
    env = v3._handle_v3_search({"workspace_id": "default", "query": "kelly", "limit": 5})
    assert env["ok"] is True
    assert isinstance(env["data"], list)
    titles = [hit["projection"]["title"] for hit in env["data"]]
    assert "Kelly sizing" in titles


def test_search_requires_query(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_search({"workspace_id": "default", "query": ""})
    assert env["ok"] is False
    assert env["error"]["code"] == "invalid_args"


def test_get_with_fields_csv(db_conn: sqlite3.Connection) -> None:
    dec_id = _seed_decision(db_conn, decision_text="The full body text here.")
    env = v3._handle_v3_get(
        {
            "workspace_id": "default",
            "kind": "decision",
            "id": dec_id,
            "fields": "decision_text,rationale",
        }
    )
    assert env["data"]["decision_text"] == "The full body text here."


def test_get_with_fields_list(db_conn: sqlite3.Connection) -> None:
    dec_id = _seed_decision(db_conn, decision_text="Body B")
    env = v3._handle_v3_get(
        {
            "workspace_id": "default",
            "kind": "decision",
            "id": dec_id,
            "fields": ["decision_text"],
        }
    )
    assert env["data"]["decision_text"] == "Body B"


# ============================================================
# Write handlers
# ============================================================


def test_write_returns_compact_projection(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_write(
        {
            "workspace_id": "default",
            "kind": "decision",
            "payload": {"title": "Hello", "decision_text": "Body"},
            "agent_id": "test-agent",
        }
    )
    assert env["ok"] is True
    assert env["data"]["kind"] == "decision"
    assert env["data"]["id"].startswith("dec_")
    assert "decision_text" not in env["data"]  # compact projection


def test_write_unsupported_kind(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_write(
        {
            "workspace_id": "default",
            "kind": "nonexistent",
            "payload": {"title": "x"},
        }
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "unsupported_kind"


def test_write_invalid_args(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_write({"workspace_id": "default", "kind": "decision"})
    assert env["ok"] is False
    assert env["error"]["code"] == "invalid_args"


def test_edit_partial_update(db_conn: sqlite3.Connection) -> None:
    dec_id = _seed_decision(db_conn, title="v1")
    env = v3._handle_v3_edit(
        {
            "workspace_id": "default",
            "kind": "decision",
            "id": dec_id,
            "fields": {"status": "superseded"},
        }
    )
    assert env["ok"] is True
    assert env["data"]["status"] == "superseded"


def test_edit_requires_fields_object(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_edit(
        {"workspace_id": "default", "kind": "decision", "id": "dec_x", "fields": "not-a-dict"}
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "invalid_args"


def test_pin_toggle(db_conn: sqlite3.Connection) -> None:
    dec_id = _seed_decision(db_conn, title="P")
    env = v3._handle_v3_pin(
        {"workspace_id": "default", "kind": "decision", "id": dec_id, "pinned": True}
    )
    assert env["ok"] is True
    assert env["data"]["pinned"] is True
    env_off = v3._handle_v3_pin(
        {"workspace_id": "default", "kind": "decision", "id": dec_id, "pinned": False}
    )
    assert env_off["data"]["pinned"] is False


def test_archive_decision(db_conn: sqlite3.Connection) -> None:
    dec_id = _seed_decision(db_conn, title="A")
    env = v3._handle_v3_archive(
        {
            "workspace_id": "default",
            "kind": "decision",
            "id": dec_id,
            "reason": "obsolete",
        }
    )
    assert env["ok"] is True
    assert env["data"]["status"] == "archived"


# ============================================================
# Hook primitives + invoke_skill
# ============================================================


def test_brief_composes_session_start(db_conn: sqlite3.Connection) -> None:
    _seed_decision(db_conn, title="One")
    env = v3._handle_v3_brief({"workspace_id": "default", "max_tokens": 500})
    assert env["ok"] is True
    assert env["data"]["token_count"] <= 500
    assert "identity" in env["data"]["sections"]


def test_lint_empty_workspace_allows(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_lint(
        {
            "workspace_id": "default",
            "tool_name": "Edit",
            "tool_payload": {"file_path": "x.py"},
        }
    )
    assert env["ok"] is True
    assert env["data"]["verdict"] == "allow"


def test_lint_invalid_tool_name(db_conn: sqlite3.Connection) -> None:
    # tool_payload defaults to {} when omitted; tool_name="" is the invalid case.
    env = v3._handle_v3_lint({"workspace_id": "default", "tool_name": "", "tool_payload": {}})
    assert env["ok"] is False
    assert env["error"]["code"] == "invalid_args"


def test_invoke_skill_not_found(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_invoke_skill({"workspace_id": "default", "skill_id": "missing"})
    assert env["ok"] is False
    assert env["error"]["code"] == "not_found"


def test_invoke_skill_requires_id(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_invoke_skill({"workspace_id": "default"})
    assert env["ok"] is False
    assert env["error"]["code"] == "invalid_args"


# ============================================================
# memory_impact_check (discipline primitive)
# ============================================================


def test_impact_check_not_indexed_envelope(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_impact_check({"workspace_id": "default", "file_path": "src/missing.py"})
    assert env["ok"] is True
    assert env["data"]["verdict"] == "not_indexed"
    assert "not in code_digests" in env["data"]["advisory"]


def test_impact_check_requires_file_path(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_impact_check({"workspace_id": "default"})
    assert env["ok"] is False
    assert env["error"]["code"] == "invalid_args"


def test_impact_check_returns_full_envelope_shape(db_conn: sqlite3.Connection) -> None:
    env = v3._handle_v3_impact_check({"workspace_id": "default", "file_path": "src/foo.py"})
    assert env["ok"] is True
    data = env["data"]
    assert set(data.keys()) == {
        "file_path",
        "digest",
        "callers",
        "hot_symbols",
        "verdict",
        "advisory",
    }
