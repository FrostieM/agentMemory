"""v3.1 active-memory MCP handlers: shape + flag passthrough."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

CANONICAL_INIT = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
CANONICAL_OUTCOME = (
    Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0002_outcome_loop.sql"
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_EXPERIMENT_PROPOSAL_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_PREDICTIVE_FAILURE_ENABLED", raising=False)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Hybrid schema — legacy + canonical overlay."""
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    db_path = tmp_path / "src.db"
    c = open_connection(db_path)
    apply_migrations(c)
    c.executescript(CANONICAL_INIT.read_text(encoding="utf-8"))
    c.executescript(CANONICAL_OUTCOME.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _patch_runtime(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    """Route ``_runtime.db_for`` + workspace resolver to our test connection."""
    from agent_memory_lite.mcp import stdio_guards, stdio_runtime  # noqa: PLC0415

    monkeypatch.setattr(stdio_runtime._runtime, "db_for", lambda _ws: conn)
    monkeypatch.setattr(
        stdio_guards,
        "_workspace_from_args",
        lambda args, intent: str(args.get("workspace_id", "ws")),
        raising=True,
    )


def test_propose_experiments_returns_envelope_shape(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty workspace → proposals=[], available=True (schema OK)."""
    _patch_runtime(monkeypatch, conn)
    from agent_memory_lite.mcp.stdio_handlers_v3_1 import (  # noqa: PLC0415
        _handle_propose_experiments,
    )

    out = _handle_propose_experiments({"workspace_id": "ws"})
    assert out["workspace_id"] == "ws"
    assert out["proposals"] == []
    assert out["persisted"] == 0
    assert out["candidate_ids"] == []
    assert out["feature_enabled"] is True
    assert out["available"] is True


def test_propose_experiments_disabled_flag(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When MEMORY_EXPERIMENT_PROPOSAL_ENABLED=false, scanner returns []
    and the response surfaces ``feature_enabled=False``."""
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_ENABLED", "false")
    _patch_runtime(monkeypatch, conn)
    from agent_memory_lite.mcp.stdio_handlers_v3_1 import (  # noqa: PLC0415
        _handle_propose_experiments,
    )

    out = _handle_propose_experiments({"workspace_id": "ws"})
    assert out["feature_enabled"] is False
    assert out["proposals"] == []


def test_predictive_warnings_returns_envelope_shape(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty workspace → warnings=[], available=True."""
    _patch_runtime(monkeypatch, conn)
    from agent_memory_lite.mcp.stdio_handlers_v3_1 import (  # noqa: PLC0415
        _handle_predictive_warnings,
    )

    out = _handle_predictive_warnings({"workspace_id": "ws"})
    assert out["workspace_id"] == "ws"
    assert out["warnings"] == []
    assert out["feature_enabled"] is True
    assert out["available"] is True


def test_predictive_warnings_disabled_flag(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORY_PREDICTIVE_FAILURE_ENABLED", "false")
    _patch_runtime(monkeypatch, conn)
    from agent_memory_lite.mcp.stdio_handlers_v3_1 import (  # noqa: PLC0415
        _handle_predictive_warnings,
    )

    out = _handle_predictive_warnings({"workspace_id": "ws"})
    assert out["feature_enabled"] is False


def test_propose_experiments_persist_flag_writes_candidates(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``persist=true`` is passed + insights have evidence
    episodes, candidate rows land. We seed a hybrid-schema insight
    to verify the round-trip.

    v3.5 fixture update: ``min_evidence`` floor raised to 3 by task
    #57 (V1 min-evidence gate + LLM noise filter). The test now
    seeds three evidence episodes so the scanner accepts the insight.
    Originally one episode was enough; that path is locked elsewhere.
    """
    _patch_runtime(monkeypatch, conn)
    # Seed three episodes + an insight that cites all of them — the
    # min-evidence gate (default 3) drops anything with fewer.
    for ep_id in ("ep_seed_1", "ep_seed_2", "ep_seed_3"):
        conn.execute(
            "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
            "VALUES (?, 'ws', 'agent_action', 'fixture', '2026-05-19T00:00:00+00:00')",
            (ep_id,),
        )
    conn.execute(
        """INSERT INTO insights
           (id, workspace_id, insight_type, summary, gist, proposed_action,
            target_type, target_id, source_episode_ids_json, confidence,
            status, tags_json, created_at, updated_at)
           VALUES ('ins_seed', 'ws', 'lesson', 'a long summary body text',
                   'a long summary body text', NULL, NULL, NULL,
                   '["ep_seed_1", "ep_seed_2", "ep_seed_3"]', 0.55, 'candidate', '[]',
                   '2026-05-19T00:00:00+00:00',
                   '2026-05-19T00:00:00+00:00')"""
    )
    conn.commit()
    from agent_memory_lite.mcp.stdio_handlers_v3_1 import (  # noqa: PLC0415
        _handle_propose_experiments,
    )

    out = _handle_propose_experiments({"workspace_id": "ws", "persist": True})
    assert out["persisted"] == 1
    assert out["candidate_ids"] == ["cand_prop_ins_seed"]
    # Verify a row landed.
    row = conn.execute(
        "SELECT kind, status FROM memory_candidates WHERE id = ?",
        ("cand_prop_ins_seed",),
    ).fetchone()
    assert row is not None
    assert row["kind"] == "theory_proposal"
    assert row["status"] == "new"


# ============================================================
# Audit-5 coverage: argument validation + parity with HTTP route
# ============================================================


def test_propose_experiments_persist_string_true_coerces(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit-5 H1: ``persist='true'`` (string) must coerce to True;
    ``persist='false'`` must coerce to False even though bool('false')
    is truthy in Python — protects against MCP wire layer string-ifying
    booleans."""
    _patch_runtime(monkeypatch, conn)
    # Three evidence episodes — see ``test_propose_experiments_persist_flag_writes_candidates``
    # for the v3.5 min-evidence rationale.
    for ep_id in ("ep_str_1", "ep_str_2", "ep_str_3"):
        conn.execute(
            "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
            "VALUES (?, 'ws', 'agent_action', 'fixture', '2026-05-19T00:00:00+00:00')",
            (ep_id,),
        )
    conn.execute(
        """INSERT INTO insights
           (id, workspace_id, insight_type, summary, gist, proposed_action,
            target_type, target_id, source_episode_ids_json, confidence,
            status, tags_json, created_at, updated_at)
           VALUES ('ins_str', 'ws', 'lesson', 'long enough body text',
                   'long enough body text', NULL, NULL, NULL,
                   '["ep_str_1", "ep_str_2", "ep_str_3"]', 0.55, 'candidate', '[]',
                   '2026-05-19T00:00:00+00:00',
                   '2026-05-19T00:00:00+00:00')"""
    )
    conn.commit()
    from agent_memory_lite.mcp.stdio_handlers_v3_1 import (  # noqa: PLC0415
        _handle_propose_experiments,
    )

    # persist='false' should NOT persist even though bool('false') is True.
    out_false = _handle_propose_experiments({"workspace_id": "ws", "persist": "false"})
    assert out_false["persisted"] == 0
    # persist='true' should persist.
    out_true = _handle_propose_experiments({"workspace_id": "ws", "persist": "true"})
    assert out_true["persisted"] == 1


def test_propose_experiments_limit_clamped_to_50(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit-5 M4: MCP handler must clamp limit to [1, 50] (HTTP parity).
    A schema-only declaration of ``maximum: 50`` is advisory; the
    handler enforces it directly."""
    _patch_runtime(monkeypatch, conn)
    from agent_memory_lite.mcp.stdio_handlers_v3_1 import _coerce_limit  # noqa: PLC0415

    # Clamp at upper bound.
    assert _coerce_limit(999) == 50
    # Clamp at lower bound.
    assert _coerce_limit(0) == 1
    assert _coerce_limit(-5) == 1
    # In-range pass-through.
    assert _coerce_limit(10) == 10
    # None → None (caller-default).
    assert _coerce_limit(None) is None
    # Garbage → None.
    assert _coerce_limit("not-an-int") is None


def test_memory_status_handler_active_memory_default_omitted(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The MCP memory_status handler honors include_active_memory=false
    by default — preserves the legacy payload shape."""
    _patch_runtime(monkeypatch, conn)
    from agent_memory_lite.mcp.stdio_handlers_memory import (  # noqa: PLC0415
        _handle_v3_status,
    )

    envelope = _handle_v3_status({"workspace_id": "ws"})
    assert envelope.get("ok") is True
    body = envelope["data"]
    assert body["active_memory"] is None


def test_memory_status_handler_active_memory_opt_in(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When include_active_memory=true, the MCP handler returns the
    dashboard with the v3.1 vector counts."""
    _patch_runtime(monkeypatch, conn)
    from agent_memory_lite.mcp.stdio_handlers_memory import (  # noqa: PLC0415
        _handle_v3_status,
    )

    envelope = _handle_v3_status({"workspace_id": "ws", "include_active_memory": True})
    assert envelope.get("ok") is True
    body = envelope["data"]
    assert body["active_memory"] is not None
    am = body["active_memory"]
    # Hybrid schema fixture (canonical overlay applied) — all flags healthy.
    assert am["proposals_available"] is True
    assert am["predictive_warnings_available"] is True
    assert am["open_proposals"] == 0
    assert am["predictive_warnings"] == 0
