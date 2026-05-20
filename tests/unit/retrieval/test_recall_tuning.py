"""v3.1 Vector 2 — recall_history persistence + suggest_params heuristic."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.retrieval import recall_tuning as rt

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "0034_recall_history.sql"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_RECALL_TUNING_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_RECALL_TUNING_WINDOW_HOURS", raising=False)
    monkeypatch.delenv("MEMORY_RECALL_TUNING_MIN_SAMPLES", raising=False)
    # v3.3 transfer-learning fallback — disable by default so legacy
    # tests that assert "below min_samples → None" keep their semantics.
    # The transfer-specific tests turn it on explicitly.
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_ENABLED", "false")
    monkeypatch.delenv("MEMORY_RECALL_TRANSFER_MIN_SAMPLES", raising=False)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415

    db_path = tmp_path / "src.db"
    c = open_connection(db_path)
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _seed(
    c: sqlite3.Connection,
    *,
    workspace_id: str,
    depth: int,
    floor: float,
    hits: int,
    outcome: float | None,
    activation: float | None = 0.5,
) -> None:
    rt.record_outcome(
        c,
        workspace_id=workspace_id,
        topic="test topic",
        depth=depth,
        outcome_floor=floor,
        hits_count=hits,
        avg_outcome=outcome,
        avg_activation=activation,
    )


def test_record_outcome_persists_row(conn: sqlite3.Connection) -> None:
    """record_outcome lands one recall_history row with normalized topic."""
    rt.record_outcome(
        conn,
        workspace_id="ws",
        topic="  Quarter-Kelly Sizing  ",
        depth=2,
        outcome_floor=0.0,
        hits_count=3,
        avg_outcome=0.42,
        avg_activation=0.55,
    )
    row = conn.execute("SELECT * FROM recall_history").fetchone()
    assert row is not None
    assert row["workspace_id"] == "ws"
    assert row["topic_norm"] == "quarter-kelly sizing"
    assert row["depth"] == 2
    assert row["outcome_floor_x100"] == 0
    assert row["hits_count"] == 3
    assert row["avg_outcome_x100"] == 42
    assert row["avg_activation_x1000"] == 550


def test_record_outcome_no_op_when_disabled(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORY_RECALL_TUNING_ENABLED", "false")
    rt.record_outcome(
        conn,
        workspace_id="ws",
        topic="t",
        depth=2,
        outcome_floor=0.0,
        hits_count=1,
        avg_outcome=0.5,
        avg_activation=0.5,
    )
    n = conn.execute("SELECT COUNT(*) FROM recall_history").fetchone()[0]
    assert n == 0


def test_suggest_params_returns_none_below_min_samples(
    conn: sqlite3.Connection,
) -> None:
    """Below MIN_SAMPLES rows → no suggestion (signal too thin)."""
    for _ in range(3):
        _seed(conn, workspace_id="ws", depth=2, floor=0.0, hits=5, outcome=0.4)
    out = rt.suggest_params(conn, workspace_id="ws", base_depth=2, base_floor=0.0)
    assert out is None


def test_suggest_params_raise_floor_when_outcome_strong(
    conn: sqlite3.Connection,
) -> None:
    """Strong avg outcome → suggest raising floor to drop noise."""
    for _ in range(10):
        _seed(conn, workspace_id="ws", depth=2, floor=0.0, hits=5, outcome=0.5)
    out = rt.suggest_params(conn, workspace_id="ws", base_depth=2, base_floor=0.0)
    assert out is not None
    assert out.reason == "raise_floor"
    assert out.depth == 2
    assert out.outcome_floor == pytest.approx(0.1)


def test_suggest_params_increase_depth_when_empties_dominate(
    conn: sqlite3.Connection,
) -> None:
    """>40% empty results → suggest depth+1, floor-0.1."""
    for _ in range(10):
        _seed(conn, workspace_id="ws", depth=1, floor=0.2, hits=0, outcome=None)
    out = rt.suggest_params(conn, workspace_id="ws", base_depth=1, base_floor=0.2)
    assert out is not None
    assert out.reason == "increase_depth"
    assert out.depth == 2
    assert out.outcome_floor == pytest.approx(0.1)


def test_suggest_params_default_when_no_rule_fires(conn: sqlite3.Connection) -> None:
    """Mid-quality results don't fire either rule — return None."""
    for _ in range(10):
        _seed(conn, workspace_id="ws", depth=2, floor=0.0, hits=2, outcome=0.1)
    out = rt.suggest_params(conn, workspace_id="ws", base_depth=2, base_floor=0.0)
    assert out is None


def test_suggest_params_caps_depth_at_3(conn: sqlite3.Connection) -> None:
    """Increase-depth rule caps at 3 even from base=3 (no-op)."""
    for _ in range(10):
        _seed(conn, workspace_id="ws", depth=3, floor=0.0, hits=0, outcome=None)
    out = rt.suggest_params(conn, workspace_id="ws", base_depth=3, base_floor=0.0)
    # Increase-depth rule has `base_depth < 3` guard — returns None.
    assert out is None


def test_suggest_params_workspace_isolation(conn: sqlite3.Connection) -> None:
    """ws_a rows do not feed ws_b's suggestion."""
    for _ in range(10):
        _seed(conn, workspace_id="ws_a", depth=2, floor=0.0, hits=5, outcome=0.5)
    out = rt.suggest_params(conn, workspace_id="ws_b", base_depth=2, base_floor=0.0)
    assert out is None


def test_suggest_params_disabled_returns_none(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORY_RECALL_TUNING_ENABLED", "false")
    for _ in range(10):
        _seed(conn, workspace_id="ws", depth=2, floor=0.0, hits=5, outcome=0.5)
    out = rt.suggest_params(conn, workspace_id="ws", base_depth=2, base_floor=0.0)
    assert out is None


def test_record_outcome_failure_soft_when_table_missing(tmp_path: Path) -> None:
    """Pre-migration DB → record_outcome silently no-ops (failure-soft)."""
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415

    c = open_connection(tmp_path / "no_table.db")
    try:
        # Should not raise.
        rt.record_outcome(
            c,
            workspace_id="ws",
            topic="t",
            depth=2,
            outcome_floor=0.0,
            hits_count=1,
            avg_outcome=0.5,
            avg_activation=0.5,
        )
    finally:
        c.close()


def test_env_helpers_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_RECALL_TUNING_WINDOW_HOURS", raising=False)
    monkeypatch.delenv("MEMORY_RECALL_TUNING_MIN_SAMPLES", raising=False)
    monkeypatch.delenv("MEMORY_RECALL_TRANSFER_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_RECALL_TRANSFER_MIN_SAMPLES", raising=False)
    assert rt.window_hours() == 72
    assert rt.min_samples() == 8
    assert rt.is_tuning_enabled() is True
    # v3.3 defaults
    assert rt.is_transfer_enabled() is True
    assert rt.transfer_min_samples() == 16


# ============================================================
# v3.3 transfer learning bootstrap
# ============================================================


def test_cold_workspace_borrows_from_siblings_in_same_db(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold workspace with <min_samples but siblings have enough →
    transfer-flavored suggestion fires."""
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_ENABLED", "true")
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_MIN_SAMPLES", "10")
    # ws_cold has just 2 samples — below own-workspace threshold (8).
    for _ in range(2):
        _seed(conn, workspace_id="ws_cold", depth=2, floor=0.0, hits=3, outcome=0.5)
    # ws_warm has 12 samples — clears the transfer-only threshold (10).
    for _ in range(12):
        _seed(conn, workspace_id="ws_warm", depth=2, floor=0.0, hits=3, outcome=0.5)
    out = rt.suggest_params(conn, workspace_id="ws_cold", base_depth=2, base_floor=0.0)
    assert out is not None
    assert out.reason.startswith("transfer:")
    # Strong avg outcome (0.5 >= 0.3 threshold) → raise_floor.
    assert "raise_floor" in out.reason


def test_cold_workspace_no_siblings_returns_none(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No history at all → no transfer suggestion."""
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_ENABLED", "true")
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_MIN_SAMPLES", "10")
    out = rt.suggest_params(conn, workspace_id="ws_cold", base_depth=2, base_floor=0.0)
    assert out is None


def test_transfer_respects_disabled_flag(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When MEMORY_RECALL_TRANSFER_ENABLED=false, cold workspace gets
    None even if siblings have enough samples."""
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_ENABLED", "false")
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_MIN_SAMPLES", "10")
    for _ in range(12):
        _seed(conn, workspace_id="ws_warm", depth=2, floor=0.0, hits=3, outcome=0.5)
    out = rt.suggest_params(conn, workspace_id="ws_cold", base_depth=2, base_floor=0.0)
    assert out is None


def test_warm_workspace_prefers_own_data_over_transfer(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the local workspace has >=min_samples, the local rules
    fire — transfer fallback is NOT consulted (and the reason has no
    'transfer:' prefix)."""
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_ENABLED", "true")
    # Local workspace clears own-workspace threshold (default 8).
    for _ in range(10):
        _seed(conn, workspace_id="ws_active", depth=2, floor=0.0, hits=3, outcome=0.5)
    out = rt.suggest_params(conn, workspace_id="ws_active", base_depth=2, base_floor=0.0)
    assert out is not None
    assert not out.reason.startswith("transfer:")


def test_transfer_threshold_below_floor_returns_none(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Siblings have history but below transfer threshold → None."""
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_ENABLED", "true")
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_MIN_SAMPLES", "20")
    for _ in range(12):  # below the higher transfer threshold
        _seed(conn, workspace_id="ws_warm", depth=2, floor=0.0, hits=3, outcome=0.5)
    out = rt.suggest_params(conn, workspace_id="ws_cold", base_depth=2, base_floor=0.0)
    assert out is None


def test_transfer_increase_depth_path(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transfer rollup with high empty-rate → increase_depth suggestion."""
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_ENABLED", "true")
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_MIN_SAMPLES", "10")
    # All 12 sibling samples returned 0 hits → empty_rate 1.0 >> 0.4
    for _ in range(12):
        _seed(conn, workspace_id="ws_warm", depth=1, floor=0.0, hits=0, outcome=None)
    out = rt.suggest_params(conn, workspace_id="ws_cold", base_depth=1, base_floor=0.0)
    assert out is not None
    assert out.reason == "transfer:increase_depth"
    assert out.depth == 2
