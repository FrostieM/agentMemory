"""v3.1 Vector 2 — handler-level integration: tuning + record_outcome.

Audit-round-3 M5 follow-up: the unit tests cover ``record_outcome`` and
``suggest_params`` in isolation. This test exercises ``_handle_recall``
end-to-end so the wiring contract is verified:

* hint applies only when caller passes no explicit param
* recall_history row lands every call regardless of hint
* ``tuning_applied`` surfaces in response only when a hint fired
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

CANONICAL_INIT = Path(__file__).resolve().parents[2] / "migrations" / "canonical" / "0001_init.sql"
RECALL_HISTORY = Path(__file__).resolve().parents[2] / "migrations" / "0034_recall_history.sql"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_RECALL_TUNING_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_RECALL_TUNING_MIN_SAMPLES", raising=False)
    monkeypatch.delenv("MEMORY_RECALL_TUNING_WINDOW_HOURS", raising=False)
    # Lower the min-samples bar so the test data fires the rule.
    monkeypatch.setenv("MEMORY_RECALL_TUNING_MIN_SAMPLES", "3")


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Apply canonical init + Vector 2 recall_history migration."""
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415

    db_path = tmp_path / "handler.db"
    c = open_connection(db_path)
    c.executescript(CANONICAL_INIT.read_text(encoding="utf-8"))
    c.executescript(RECALL_HISTORY.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _seed_recall_history(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    rows: int,
    avg_outcome: float,
) -> None:
    """Backfill recall_history to feed the suggest_params heuristic."""
    from agent_memory_lite.retrieval.recall_tuning import record_outcome  # noqa: PLC0415

    for _ in range(rows):
        record_outcome(
            conn,
            workspace_id=workspace_id,
            topic="test topic",
            depth=2,
            outcome_floor=0.0,
            hits_count=5,
            avg_outcome=avg_outcome,
            avg_activation=0.5,
        )


def _patch_handler_runtime(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    """Make ``_runtime.db_for`` return our test connection."""
    from agent_memory_lite.mcp import stdio_runtime  # noqa: PLC0415

    monkeypatch.setattr(stdio_runtime._runtime, "db_for", lambda _workspace_id: conn, raising=True)

    # _workspace_from_args may need the registry to resolve — short-circuit it.
    from agent_memory_lite.mcp import stdio_guards  # noqa: PLC0415

    monkeypatch.setattr(
        stdio_guards,
        "_workspace_from_args",
        lambda args, intent: str(args.get("workspace_id", "ws")),
        raising=True,
    )


def _patch_recall_to_return_known_hits(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Make ``recall.recall`` return a 3-hit deterministic shape.

    Returns the same hits list both for tracing and for assertion.

    Patches the binding INSIDE the handler module — not the source —
    because ``stdio_handlers_recall.py`` did ``from ...recall import
    recall`` and a source-side patch would be a no-op in full-suite
    test runs."""
    from agent_memory_lite.mcp import stdio_handlers_recall  # noqa: PLC0415

    class _FakeHit:
        def __init__(self, oscore: float) -> None:
            self.kind = "decision"
            self.object_id = "dec_x"
            self.activation = 0.7
            self.hops = 1
            self.outcome_score = oscore
            self.projection = {"id": "dec_x"}
            self.causal_links: list[dict] = []
            self.score = 0.7

    fake_hits = [_FakeHit(0.5), _FakeHit(0.4), _FakeHit(0.3)]
    monkeypatch.setattr(stdio_handlers_recall, "recall", lambda *a, **kw: fake_hits, raising=True)
    return fake_hits  # type: ignore[return-value]


def test_handler_applies_hint_when_caller_omits_params(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No explicit depth/floor + strong-outcome history → raise_floor hint."""
    _seed_recall_history(conn, workspace_id="ws", rows=6, avg_outcome=0.5)
    _patch_handler_runtime(monkeypatch, conn)
    _patch_recall_to_return_known_hits(monkeypatch)
    from agent_memory_lite.mcp.stdio_handlers_recall import _handle_recall  # noqa: PLC0415

    out = _handle_recall({"workspace_id": "ws", "topic": "test topic"})
    assert out["tuning_applied"] == "raise_floor"
    assert "outcome_floor" in out
    assert len(out["hits"]) == 3


def test_handler_skips_hint_when_caller_passes_depth(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit ``depth=`` disables the hint path entirely."""
    _seed_recall_history(conn, workspace_id="ws", rows=6, avg_outcome=0.5)
    _patch_handler_runtime(monkeypatch, conn)
    _patch_recall_to_return_known_hits(monkeypatch)
    from agent_memory_lite.mcp.stdio_handlers_recall import _handle_recall  # noqa: PLC0415

    out = _handle_recall({"workspace_id": "ws", "topic": "test topic", "depth": 1})
    assert "tuning_applied" not in out
    assert out["depth"] == 1


def test_handler_logs_recall_history_every_call(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every successful recall call writes one row regardless of hint."""
    _patch_handler_runtime(monkeypatch, conn)
    _patch_recall_to_return_known_hits(monkeypatch)
    from agent_memory_lite.mcp.stdio_handlers_recall import _handle_recall  # noqa: PLC0415

    before = conn.execute("SELECT COUNT(*) FROM recall_history").fetchone()[0]
    _handle_recall({"workspace_id": "ws", "topic": "test topic"})
    after = conn.execute("SELECT COUNT(*) FROM recall_history").fetchone()[0]
    assert after == before + 1


def test_handler_logs_recall_history_when_disabled_via_explicit_param(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even when caller bypasses the hint by passing explicit params,
    record_outcome still lands a row so the rollup learns from the
    explicit call."""
    _patch_handler_runtime(monkeypatch, conn)
    _patch_recall_to_return_known_hits(monkeypatch)
    from agent_memory_lite.mcp.stdio_handlers_recall import _handle_recall  # noqa: PLC0415

    _handle_recall(
        {
            "workspace_id": "ws",
            "topic": "test topic",
            "depth": 3,
            "outcome_floor": 0.1,
        }
    )
    rows = conn.execute(
        "SELECT depth, outcome_floor_x100 FROM recall_history WHERE workspace_id = 'ws'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 3
    assert rows[0][1] == 10  # outcome_floor=0.1 → x100 = 10
