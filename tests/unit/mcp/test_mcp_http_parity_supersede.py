"""Round-2 MCP-HTTP parity audit: implicit supersede feedback.

The HTTP route ``POST /memory/write_decision`` calls
``record_implicit_supersede`` after writing a decision whose
``supersedes_decision_id`` is set. This drops the SUPERSEDED row's
``outcome_score`` on next refresh so the brief stops surfacing it.

Three MCP write surfaces used to skip the call entirely, breaking the
Phase 1 outcome loop for any MCP-only deployment (HTTP service down,
or a local-fallback path that didn't reach the HTTP route):

* ``tools_decisions.memory_write_decision`` (in-process MCP)
* ``stdio_handlers_decisions._handle_write_decision`` (local fallback)
* ``tools_compound.memory_record_with_evidence`` (local fallback for
  the compound write)

All three now route through the shared ``record_supersede_feedback``
helper in ``ingestion/_write_helpers``. This test locks the contract:
after each MCP write, the ``memory_usage_feedback`` table carries a row that
points at the SUPERSEDED decision with ``source="implicit_supersede"``
and ``usefulness=-0.6`` — matching what the HTTP route emits.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.api.agent_context import (
    reset_current_agent_id,
    set_current_agent_id,
)
from agent_memory_lite.db.connection import open_connection
from agent_memory_lite.db.migrations import apply_migrations


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = open_connection(tmp_path / "parity.db")
    apply_migrations(conn)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _clear_agent() -> Iterator[None]:
    reset_current_agent_id()
    set_current_agent_id("parity-test")
    yield
    reset_current_agent_id()


def _seed_decision(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    decision_id: str,
    title: str = "Prior decision",
) -> None:
    """Seed a decision the MCP write will then supersede. Active +
    unpinned so the outcome loop is the only thing that can knock it
    out of the brief."""
    now = "2026-05-19T00:00:00+00:00"
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, rationale, status,
            supersedes_decision_id, source_episode_id, confidence,
            importance, valid_from, valid_to, created_at, updated_at,
            pinned)
           VALUES (?, ?, ?, 'prior text', NULL, 'active', NULL, NULL, 0.9,
                   0.8, ?, NULL, ?, ?, 0)""",
        (decision_id, workspace_id, title, now, now, now),
    )
    conn.commit()


def _supersede_feedback_rows(
    conn: sqlite3.Connection, *, workspace_id: str, source_id: str
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """SELECT source_id, source_type, source, usefulness
               FROM memory_usage_feedback
               WHERE workspace_id = ? AND source_id = ?
                 AND source = 'implicit_supersede'""",
            (workspace_id, source_id),
        ).fetchall()
    )


def test_in_process_write_decision_emits_supersede_feedback(
    db: sqlite3.Connection,
) -> None:
    """``tools_decisions.memory_write_decision`` (in-process MCP) must
    drop the SUPERSEDED decision's outcome via implicit feedback."""
    from agent_memory_lite.mcp.tools_decisions import memory_write_decision  # noqa: PLC0415

    _seed_decision(db, workspace_id="alpha", decision_id="dec_old")
    response = memory_write_decision(
        conn=db,
        payload={
            "workspace_id": "alpha",
            "title": "Replacement",
            "decision_text": "Adopt the new approach.",
            "supersedes_decision_id": "dec_old",
            "allow_orphan": True,
        },
    )
    assert response["status"] == "active"

    rows = _supersede_feedback_rows(db, workspace_id="alpha", source_id="dec_old")
    assert len(rows) == 1, (
        "MCP in-process write_decision must emit implicit_supersede on "
        "the OLD decision to match HTTP write_decision"
    )
    row = rows[0]
    assert row["source_type"] == "decision"
    assert row["usefulness"] == pytest.approx(-0.6)


def test_stdio_local_fallback_write_decision_emits_supersede_feedback(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``stdio_handlers_decisions._handle_write_decision`` falls back to
    the in-process path when the HTTP service is unreachable. That
    fallback must emit the same supersede feedback as the HTTP route."""
    # Force the local-fallback branch: make _http_write return None.
    # stdio_handlers_decisions imports _http_write at module top so the
    # binding is already resolved — patch on the consumer module, not
    # on the source module, otherwise the patch is a no-op.
    from agent_memory_lite.mcp import stdio_handlers_decisions  # noqa: PLC0415
    from agent_memory_lite.mcp.stdio_handlers_decisions import (  # noqa: PLC0415
        _handle_write_decision,
    )
    from agent_memory_lite.mcp.stdio_runtime import _runtime  # noqa: PLC0415

    monkeypatch.setattr(stdio_handlers_decisions, "_http_write", lambda *a, **kw: None)
    # Pin the runtime's db_for() at our test connection so the local
    # fallback writes against the test DB.
    monkeypatch.setattr(_runtime, "db_for", lambda _ws: db)

    _seed_decision(db, workspace_id="alpha", decision_id="dec_old_stdio")
    response = _handle_write_decision(
        {
            "workspace_id": "alpha",
            "title": "Replacement (stdio fallback)",
            "decision_text": "Adopt the new approach via stdio MCP.",
            "supersedes_decision_id": "dec_old_stdio",
            "allow_orphan": True,
        }
    )
    assert response["superseded_decision_id"] == "dec_old_stdio"

    rows = _supersede_feedback_rows(db, workspace_id="alpha", source_id="dec_old_stdio")
    assert len(rows) == 1, (
        "stdio MCP local-fallback write_decision must emit "
        "implicit_supersede to match the HTTP route"
    )
    assert rows[0]["usefulness"] == pytest.approx(-0.6)


def test_compound_record_with_evidence_emits_supersede_feedback(
    db: sqlite3.Connection,
) -> None:
    """``tools_compound.memory_record_with_evidence`` is the MCP local
    fallback for /memory/record_with_evidence. It must emit the same
    supersede feedback as the HTTP route AND thread settings through
    so ingest_episode honors auto_promote env flags."""
    from agent_memory_lite.mcp.tools_compound import (  # noqa: PLC0415
        memory_record_with_evidence,
    )

    _seed_decision(db, workspace_id="alpha", decision_id="dec_old_compound")
    response = memory_record_with_evidence(
        conn=db,
        embedding_provider=None,
        vector_store=None,
        payload={
            "workspace_id": "alpha",
            "evidence_text": "Replacement evidence: the prior decision was wrong.",
            "decision_title": "Replacement (compound)",
            "decision_text": "Adopt the new approach.",
            "supersedes_decision_id": "dec_old_compound",
        },
    )
    assert response["decision_status"] == "active"
    assert response["superseded_decision_id"] == "dec_old_compound"

    rows = _supersede_feedback_rows(db, workspace_id="alpha", source_id="dec_old_compound")
    assert len(rows) == 1, (
        "MCP compound record_with_evidence must emit implicit_supersede "
        "to match the HTTP /memory/record_with_evidence route"
    )
    assert rows[0]["usefulness"] == pytest.approx(-0.6)


def test_no_supersede_means_no_feedback_row(db: sqlite3.Connection) -> None:
    """Sanity: when the write does NOT supersede anything, no implicit
    supersede row gets emitted. Locks the helper's null-guard."""
    from agent_memory_lite.mcp.tools_decisions import memory_write_decision  # noqa: PLC0415

    response = memory_write_decision(
        conn=db,
        payload={
            "workspace_id": "alpha",
            "title": "Standalone decision",
            "decision_text": "No prior to supersede.",
            "allow_orphan": True,
        },
    )
    assert response["status"] == "active"

    feedback_count = db.execute(
        "SELECT COUNT(*) FROM memory_usage_feedback WHERE source = 'implicit_supersede'"
    ).fetchone()[0]
    assert feedback_count == 0
