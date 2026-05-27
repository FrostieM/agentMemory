"""v3.4 #8 — workspace audit_log signals for V5 LR.

Covers the new ``workspace_audit_signals`` helper (velocity /
edit_share / agent_diversity, all in [0, 1]) plus the integration
into ``featurize`` (extra slots), the bumped model schema
(``audit_dim`` round-trips), and the leakage-safe ``as_of_ts``
contract that lets training samples see only audit rows that
existed before the sample's own created_at.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from agent_memory_lite.maintenance.predictive_lr import (
    LRModel,
    load_model,
    predict_negative_outcome,
    save_model,
)
from agent_memory_lite.maintenance.predictive_lr_audit import (
    AUDIT_FEATURE_COUNT,
    workspace_audit_signals,
)
from agent_memory_lite.maintenance.predictive_lr_features import (
    feature_dim,
    featurize,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MEMORY_PREDICTIVE_LR_AUDIT_WINDOW_DAYS",
        "MEMORY_PREDICTIVE_LR_AUDIT_VELOCITY_CEILING",
        "MEMORY_PREDICTIVE_LR_AUDIT_DIVERSITY_CEILING",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """Connection with the canonical schema (includes audit_log)."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def _add_audit(
    conn: sqlite3.Connection,
    *,
    action: str,
    days_ago: int,
    agent_id: str | None = None,
    ws: str = "ws",
    row_id: str | None = None,
) -> None:
    when = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    aid = row_id or f"a_{action}_{days_ago}_{agent_id or 'none'}"
    conn.execute(
        """INSERT INTO audit_log
           (id, workspace_id, action, target_type, target_id,
            source_episode_id, before_json, after_json, created_at, agent_id)
           VALUES (?, ?, ?, 'decision', 't', NULL, '{}', '{}', ?, ?)""",
        (aid, ws, action, when, agent_id),
    )
    conn.commit()


# ============================================================
# workspace_audit_signals
# ============================================================


def test_no_rows_returns_zeros(conn: sqlite3.Connection) -> None:
    v, e, d = workspace_audit_signals(conn, workspace_id="ws")
    assert (v, e, d) == (0.0, 0.0, 0.0)


def test_velocity_normalized(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """7-day window, velocity_ceiling=10/day → 7 rows/day saturates < 1."""
    monkeypatch.setenv("MEMORY_PREDICTIVE_LR_AUDIT_VELOCITY_CEILING", "10")
    for i in range(7):
        _add_audit(conn, action="ingest_episode", days_ago=i % 7, row_id=f"a_{i}")
    v, _, _ = workspace_audit_signals(conn, workspace_id="ws")
    # 7 rows over 7 days = 1 row/day; ceiling=10 → velocity=0.1.
    assert v == pytest.approx(0.1)


def test_edit_share_distinguishes_mutations(conn: sqlite3.Connection) -> None:
    """Mix of mutating (edit/archive) and non-mutating (search/brief)
    actions → edit_share matches the mutation fraction."""
    for i in range(3):
        _add_audit(conn, action="edit", days_ago=i, row_id=f"a_edit_{i}")
    for i in range(3):
        _add_audit(conn, action="search", days_ago=i, row_id=f"a_search_{i}")
    _, e, _ = workspace_audit_signals(conn, workspace_id="ws")
    assert e == pytest.approx(0.5)  # 3 of 6 are mutating


def test_agent_diversity_caps_at_ceiling(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """7 distinct agent_ids with diversity_ceiling=5 → diversity = 1.0."""
    monkeypatch.setenv("MEMORY_PREDICTIVE_LR_AUDIT_DIVERSITY_CEILING", "5")
    for i in range(7):
        _add_audit(
            conn, action="ingest_episode", days_ago=i % 5, agent_id=f"agent_{i}", row_id=f"a_{i}"
        )
    _, _, d = workspace_audit_signals(conn, workspace_id="ws")
    assert d == 1.0


def test_as_of_ts_excludes_future_rows(conn: sqlite3.Connection) -> None:
    """as_of_ts is leakage-safe: rows created at-or-after the timestamp
    are invisible to the training sample's view of the workspace."""
    past = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    future = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    # Row 1 day ago — INVISIBLE to a sample dated 3 days ago.
    conn.execute(
        """INSERT INTO audit_log
           (id, workspace_id, action, target_type, target_id,
            source_episode_id, before_json, after_json, created_at, agent_id)
           VALUES ('a_recent', 'ws', 'edit', 'decision', 't', NULL, '{}', '{}', ?, NULL)""",
        (future,),
    )
    conn.commit()
    v, _, _ = workspace_audit_signals(conn, workspace_id="ws", as_of_ts=past)
    assert v == 0.0  # no rows before `past`


def test_failure_soft_missing_table() -> None:
    """Pre-migration DB without audit_log → zeros, not exception."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    try:
        assert workspace_audit_signals(c, workspace_id="ws") == (0.0, 0.0, 0.0)
    finally:
        c.close()


# ============================================================
# featurize + LRModel integration
# ============================================================


def test_featurize_appends_audit_slots() -> None:
    """Passing audit_signals lengthens the vector by AUDIT_FEATURE_COUNT."""
    vocab = ["alpha", "beta"]
    base = featurize(text="alpha", vocab=vocab, recent_outcomes=[0.0], trail_length=2)
    assert len(base) == feature_dim(vocab)  # 2 + 2 = 4
    enriched = featurize(
        text="alpha",
        vocab=vocab,
        recent_outcomes=[0.0],
        trail_length=2,
        audit_signals=(0.1, 0.4, 0.7),
    )
    assert len(enriched) == feature_dim(vocab, audit_dim=AUDIT_FEATURE_COUNT)
    # Last three slots match the audit signal in order.
    assert enriched[-3:] == [0.1, 0.4, 0.7]


def test_featurize_clamps_audit_signals_to_unit() -> None:
    """A caller passing un-normalized signals does not blow up the
    vector (the model expects [0, 1] per slot)."""
    vocab = ["foo"]
    out = featurize(
        text="foo",
        vocab=vocab,
        recent_outcomes=[],
        trail_length=0,
        audit_signals=(2.5, -0.1, 0.5),
    )
    assert out[-3:] == [1.0, 0.0, 0.5]


def test_lrmodel_round_trip_preserves_audit_dim(conn: sqlite3.Connection) -> None:
    """Save + load preserves audit_dim so the predict path knows how
    many audit slots the weight vector covers."""
    model = LRModel(
        vocab=["a"],
        weights=[0.1, 0.0, 0.0, 0.2, 0.3, 0.4],
        bias=0.0,
        trained_at="now",
        n_samples=99,
        audit_dim=3,
    )
    assert save_model(conn, workspace_id="ws", model=model)
    loaded = load_model(conn, workspace_id="ws")
    assert loaded is not None
    assert loaded.audit_dim == 3
    assert len(loaded.weights) == feature_dim(loaded.vocab, audit_dim=loaded.audit_dim)


def test_predict_with_audit_signals_runs() -> None:
    """A model trained with audit_dim=3 accepts audit_signals at predict
    time and returns a probability in [0, 1]."""
    model = LRModel(
        vocab=["foo"],
        weights=[1.0, 0.0, 0.0, -0.5, -0.5, 1.0],  # 1 vocab + trend + trail + 3 audit
        bias=0.0,
        trained_at="now",
        n_samples=100,
        audit_dim=3,
    )
    p = predict_negative_outcome(
        model,
        text="foo bar",
        recent_outcomes=[0.0],
        trail_length=1,
        audit_signals=(0.2, 0.5, 0.3),
    )
    assert p is not None
    assert 0.0 <= p <= 1.0


def test_predict_legacy_model_ignores_audit_signals() -> None:
    """A model with audit_dim=0 (older shape) silently drops audit
    signals — keeps inference working until the next training cycle
    refreshes the model schema."""
    model = LRModel(
        vocab=["foo"],
        weights=[1.0, 0.0, 0.0],  # 1 vocab + trend + trail, no audit
        bias=0.0,
        trained_at="now",
        n_samples=100,
        audit_dim=0,
    )
    p = predict_negative_outcome(
        model,
        text="foo",
        recent_outcomes=[0.0],
        trail_length=1,
        audit_signals=(0.9, 0.9, 0.9),  # provided but ignored
    )
    assert p is not None  # legacy path keeps working
