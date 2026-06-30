"""v3.3 Vector 5 LR predictor tests.

Locks the SGD core (convergence on a separable toy dataset),
persistence round-trip, predict ergonomics, and the failure-soft
contract for missing schema / untrained workspace.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.maintenance.predictive_lr import (
    LRModel,
    _train_sgd,
    is_enabled,
    load_model,
    predict_negative_outcome,
    save_model,
    train_workspace,
)
from agent_memory_lite.maintenance.predictive_lr_features import (
    build_vocab,
    feature_dim,
    featurize,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # M4 (global-audit 2026-06-30): the feature is now OPT-IN (default off). This
    # module exercises the training behaviour, so it enables it explicitly; the
    # disabled-path and opt-in-default tests override this within their own bodies.
    monkeypatch.setenv("MEMORY_PREDICTIVE_LR_ENABLED", "true")
    monkeypatch.delenv("MEMORY_PREDICTIVE_LR_MIN_SAMPLES", raising=False)
    monkeypatch.delenv("MEMORY_PREDICTIVE_LR_EPOCHS", raising=False)
    monkeypatch.delenv("MEMORY_PREDICTIVE_LR_LEARNING_RATE", raising=False)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def _seed_decision(
    conn: sqlite3.Connection,
    *,
    dec_id: str,
    title: str,
    text: str,
    outcome: float,
    created_at: str,
) -> None:
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, rationale,
            outcome_score, status, valid_from, created_at, updated_at)
           VALUES (?, 'ws', ?, ?, '', ?, 'active', ?, ?, ?)""",
        (dec_id, title, text, outcome, created_at, created_at, created_at),
    )
    conn.commit()


# ============================================================
# SGD core
# ============================================================


def test_sgd_converges_on_separable_data() -> None:
    """Toy data: label=1 iff first feature > 0.5. After SGD the model
    should classify these with high accuracy."""
    samples = []
    for i in range(50):
        x = i / 49.0  # 0.0 .. 1.0
        features = [x, 1.0 - x]  # 2-D
        label = 1 if x > 0.5 else 0
        samples.append((features, label))
    weights, bias, loss = _train_sgd(samples, dim=2, n_epochs=50, lr=0.1)
    # First weight should be strongly positive (correlates with label).
    assert weights[0] > weights[1]
    # Loss should be small after convergence.
    assert loss < 0.5
    # Sanity check: high-feature point predicts ~1, low predicts ~0.
    import math  # noqa: PLC0415

    high_z = weights[0] * 0.9 + weights[1] * 0.1 + bias
    low_z = weights[0] * 0.1 + weights[1] * 0.9 + bias
    assert 1.0 / (1.0 + math.exp(-high_z)) > 0.7
    assert 1.0 / (1.0 + math.exp(-low_z)) < 0.3


# ============================================================
# Feature extraction
# ============================================================


def test_build_vocab_picks_top_k_deterministic() -> None:
    """Build_vocab should be stable: same input → same vocab order."""
    texts = ["alpha beta gamma", "alpha beta delta", "alpha epsilon"]
    v1 = build_vocab(texts, size=4)
    v2 = build_vocab(texts, size=4)
    assert v1 == v2
    # alpha appears in all 3 docs → highest df → first in vocab.
    assert v1[0] == "alpha"


def test_featurize_returns_correct_dim() -> None:
    vocab = ["alpha", "beta", "gamma"]
    f = featurize(
        text="alpha gamma extra",
        vocab=vocab,
        recent_outcomes=[0.5, -0.5],
        trail_length=3,
    )
    assert len(f) == feature_dim(vocab) == 5  # 3 vocab + 2 extras
    # alpha present, beta absent, gamma present
    assert f[0] == 1.0
    assert f[1] == 0.0
    assert f[2] == 1.0
    # Trend slot: avg([0.5, -0.5]) = 0.0 → rescaled to 0.5
    assert f[3] == pytest.approx(0.5)


# ============================================================
# Persistence round-trip
# ============================================================


def test_save_and_load_model_round_trip(conn: sqlite3.Connection) -> None:
    model = LRModel(
        vocab=["foo", "bar"],
        weights=[0.5, -0.5],
        bias=0.1,
        trained_at="2026-05-20T12:00:00+00:00",
        n_samples=42,
    )
    assert save_model(conn, workspace_id="ws", model=model) is True
    loaded = load_model(conn, workspace_id="ws")
    assert loaded is not None
    assert loaded.vocab == ["foo", "bar"]
    assert loaded.weights == [0.5, -0.5]
    assert loaded.bias == pytest.approx(0.1)
    assert loaded.n_samples == 42


def test_load_model_returns_none_on_miss(conn: sqlite3.Connection) -> None:
    assert load_model(conn, workspace_id="never_trained") is None


def test_load_model_failure_soft_missing_table() -> None:
    """workspace_meta missing → load returns None instead of raising."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    try:
        assert load_model(c, workspace_id="ws") is None
    finally:
        c.close()


# ============================================================
# Prediction
# ============================================================


def test_predict_returns_none_without_model() -> None:
    """No model → caller falls back to legacy Jaccard V5."""
    assert (
        predict_negative_outcome(None, text="anything", recent_outcomes=[], trail_length=0) is None
    )


def test_predict_returns_probability_in_range() -> None:
    """Predict on a real model returns ∈ [0, 1]. ``weights`` must
    cover full feature_dim (vocab + trend + trail = len(vocab) + 2)."""
    vocab = ["kelly", "drawdown"]
    model = LRModel(
        vocab=vocab,
        weights=[2.0, -1.0, 0.1, 0.1],  # vocab + trend + trail
        bias=0.0,
        trained_at="now",
        n_samples=50,
    )
    p = predict_negative_outcome(
        model,
        text="kelly sizing affects drawdown",
        recent_outcomes=[0.0],
        trail_length=5,
    )
    assert p is not None
    assert 0.0 <= p <= 1.0


# ============================================================
# train_workspace
# ============================================================


def test_train_workspace_below_min_samples_skips(conn: sqlite3.Connection) -> None:
    """Workspace with too few decisions → trained=False."""
    for i in range(5):
        _seed_decision(
            conn,
            dec_id=f"dec_{i}",
            title=f"title {i}",
            text=f"text {i}",
            outcome=0.0,
            created_at=f"2026-05-{i + 1:02d}T00:00:00+00:00",
        )
    report = train_workspace(conn, workspace_id="ws", now_iso="2026-05-20T00:00:00+00:00")
    assert report.trained is False
    assert report.samples == 5


def test_train_workspace_persists_model(conn: sqlite3.Connection) -> None:
    """Past min_samples → model trains + lands in workspace_meta."""
    # Seed 30 decisions: half negative outcome, half positive.
    for i in range(30):
        outcome = -0.5 if i < 15 else 0.5
        title = "failure case" if i < 15 else "success case"
        _seed_decision(
            conn,
            dec_id=f"dec_{i}",
            title=title,
            text=f"body for {i}",
            outcome=outcome,
            created_at=f"2026-04-{(i % 28) + 1:02d}T00:00:00+00:00",
        )
    report = train_workspace(conn, workspace_id="ws", now_iso="2026-05-20T00:00:00+00:00")
    assert report.trained is True
    assert report.samples == 30
    loaded = load_model(conn, workspace_id="ws")
    assert loaded is not None
    assert loaded.n_samples == 30


def test_predictive_lr_is_opt_in_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """M4 (global-audit 2026-06-30): the model trains fine but its inference is not
    wired into any consumer, so default-on just trained an unused model every brain
    pass. Training is now OPT-IN -- is_enabled() is False unless explicitly turned on."""
    monkeypatch.delenv("MEMORY_PREDICTIVE_LR_ENABLED", raising=False)
    assert is_enabled() is False
    monkeypatch.setenv("MEMORY_PREDICTIVE_LR_ENABLED", "1")
    assert is_enabled() is True


def test_disabled_skips_training(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_PREDICTIVE_LR_ENABLED", "false")
    for i in range(30):
        _seed_decision(
            conn,
            dec_id=f"dec_{i}",
            title=f"t {i}",
            text="body",
            outcome=0.0,
            created_at=f"2026-04-{(i % 28) + 1:02d}T00:00:00+00:00",
        )
    report = train_workspace(conn, workspace_id="ws", now_iso="now")
    assert report.trained is False
