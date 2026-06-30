"""v3.3 Vector 5 LR predictor — pure-stdlib Logistic Regression.

Roadmap V5 called for a classifier on ``audit_log`` features that
predicts whether the next 10 episodes' outcome trend will go negative
given the current tool call. v3.1 shipped a Jaccard heuristic instead
(cheap but not learned). This module adds the real classifier.

Why pure-stdlib SGD vs sklearn: the project is mandatory-local-only,
sklearn pulls scipy + numpy as transitive deps (~50MB), and a small
LR over a 50-token vocab + 2 extras converges in <1s on the hot path
without them. The math is 12 lines.

Two-step pipeline:

* ``train_workspace`` — collect ``(features, label)`` samples from
  the decision rows + outcome history, run SGD, persist the model.
  Returns ``None`` when not enough samples to train (default 30).
* ``predict_negative_outcome`` — given a candidate decision text +
  current trend / trail, return ``prob in [0, 1]`` that the next
  outcome trend will go negative. Caller wires this into the lint /
  warning path.

Persistence: weights + vocab + meta land in ``workspace_meta`` under
key ``predictive_lr_model_v1`` as JSON. Failure-soft: if the meta
table is missing, train/predict return None and the legacy Jaccard
V5 path keeps the warning surface alive.

Settings: ``MEMORY_PREDICTIVE_LR_ENABLED`` (default FALSE -- opt-in; trains a model
whose inference is not yet wired, so it stays off to avoid an unused model),
``..._MIN_SAMPLES`` (30), ``..._EPOCHS`` (20), ``..._LEARNING_RATE`` (0.1).
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from dataclasses import asdict, dataclass, field

from agent_memory_lite.maintenance.predictive_lr_audit import (
    AUDIT_FEATURE_COUNT,
    workspace_audit_signals,
)
from agent_memory_lite.maintenance.predictive_lr_features import (
    VOCAB_SIZE,
    build_vocab,
    feature_dim,
    featurize,
)

# v3.4 #8: bumped from v1 to v2 because the feature vector grew by
# AUDIT_FEATURE_COUNT slots. Old v1 model rows stay in workspace_meta
# but no longer load — train_workspace runs from scratch on first call.
_META_KEY = "predictive_lr_model_v2"


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, floor: int = 1) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return max(floor, int(raw))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def is_enabled() -> bool:
    # M4 (global-audit 2026-06-30): OPT-IN. The model trains fine, but its inference
    # (predict_negative_outcome) is not yet wired into any consumer -- training it on
    # every brain pass by default just burned cycles to produce a model nothing reads.
    # Default OFF until a consumer (e.g. the lint warning path) is wired up; an
    # operator can still opt in with MEMORY_PREDICTIVE_LR_ENABLED=1.
    return _bool_env("MEMORY_PREDICTIVE_LR_ENABLED", False)


def min_samples() -> int:
    return _int_env("MEMORY_PREDICTIVE_LR_MIN_SAMPLES", 30)


def epochs() -> int:
    return _int_env("MEMORY_PREDICTIVE_LR_EPOCHS", 20)


def learning_rate() -> float:
    return _float_env("MEMORY_PREDICTIVE_LR_LEARNING_RATE", 0.1)


@dataclass(frozen=True, slots=True)
class LRModel:
    """Serialized LR model. ``weights`` and ``vocab`` align by index.

    ``audit_dim`` (v3.4 #8) records how many trailing audit_log slots
    the feature vector carried at train time. Predict must match.
    A v1 model loaded from a workspace_meta row written by the older
    code path has audit_dim=0 by default."""

    vocab: list[str]
    weights: list[float]
    bias: float
    trained_at: str
    n_samples: int
    version: str = "lr_v2"
    threshold: float = 0.6
    audit_dim: int = 0


@dataclass(slots=True)
class TrainReport:
    """Per-workspace training summary surfaced from brain_pass."""

    samples: int
    trained: bool
    final_loss: float = 0.0
    errors: list[str] = field(default_factory=list)


def _sigmoid(z: float) -> float:
    # Cheap clamp to avoid math.exp overflow on extreme weights.
    if z >= 35.0:
        return 1.0
    if z <= -35.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


def _dot(weights: list[float], features: list[float]) -> float:
    return sum(w * f for w, f in zip(weights, features, strict=True))


def _train_sgd(
    samples: list[tuple[list[float], int]],
    *,
    dim: int,
    n_epochs: int,
    lr: float,
) -> tuple[list[float], float, float]:
    """Plain SGD with logistic loss. Returns (weights, bias, final_loss)."""
    weights = [0.0] * dim
    bias = 0.0
    final_loss = 0.0
    for _ in range(n_epochs):
        epoch_loss = 0.0
        for features, label in samples:
            z = _dot(weights, features) + bias
            pred = _sigmoid(z)
            err = pred - label
            # Cross-entropy loss, clamped for log(0).
            p_clamped = min(max(pred, 1e-9), 1 - 1e-9)
            epoch_loss += -(label * math.log(p_clamped) + (1 - label) * math.log(1 - p_clamped))
            for i, f in enumerate(features):
                weights[i] -= lr * err * f
            bias -= lr * err
        final_loss = epoch_loss / max(1, len(samples))
    return weights, bias, final_loss


def save_model(conn: sqlite3.Connection, *, workspace_id: str, model: LRModel) -> bool:
    """Persist model JSON in workspace_meta. False on missing table."""
    try:
        conn.execute(
            "INSERT OR REPLACE INTO workspace_meta (workspace_id, key, value, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (workspace_id, _META_KEY, json.dumps(asdict(model)), model.trained_at),
        )
        conn.commit()
    except sqlite3.OperationalError:
        return False
    return True


def load_model(conn: sqlite3.Connection, *, workspace_id: str) -> LRModel | None:
    """Read model JSON from workspace_meta. None on miss / parse error."""
    try:
        row = conn.execute(
            "SELECT value FROM workspace_meta WHERE workspace_id = ? AND key = ?",
            (workspace_id, _META_KEY),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    try:
        raw = row[0] if isinstance(row, tuple | sqlite3.Row) else row
        payload = json.loads(raw)
        return LRModel(**payload)
    except (ValueError, TypeError):
        return None


def predict_negative_outcome(
    model: LRModel | None,
    *,
    text: str,
    recent_outcomes: list[float],
    trail_length: int,
    audit_signals: tuple[float, ...] | None = None,
) -> float | None:
    """Return probability ∈ [0, 1] that the candidate decision will
    lead to a negative outcome trend. ``None`` when no model trained.

    ``audit_signals`` (v3.4 #8): must match the model's ``audit_dim``.
    Pass ``workspace_audit_signals(conn, ...)`` from the caller. When
    a model was trained with ``audit_dim=0`` (legacy) any provided
    audit_signals are silently dropped, keeping the older inference
    path working until the next training cycle refreshes the model."""
    if model is None:
        return None
    use_audit = audit_signals if model.audit_dim else None
    features = featurize(
        text=text,
        vocab=model.vocab,
        recent_outcomes=recent_outcomes,
        trail_length=trail_length,
        audit_signals=use_audit,
    )
    if len(features) != feature_dim(model.vocab, audit_dim=model.audit_dim):
        # Mismatch can happen if the caller passed the wrong audit_dim
        # against an older model. Fail-soft: no prediction.
        return None  # pragma: no cover - defensive
    return _sigmoid(_dot(model.weights, features) + model.bias)


def train_workspace(conn: sqlite3.Connection, *, workspace_id: str, now_iso: str) -> TrainReport:
    """Mine training samples from the decisions table and run SGD.

    Sample shape: each historical decision becomes one row with
    ``label = 1`` if its outcome_score < -0.2 (negative-trend
    confirmed) else 0, and ``features = featurize(decision_text +
    rationale + title, vocab, recent outcomes window, trail length)``.

    The "recent outcomes" at training time are the prior K decisions'
    outcome scores ordered by created_at — same recipe predict_*
    callers use at inference, so train/predict see aligned features.
    """
    if not is_enabled():
        return TrainReport(samples=0, trained=False)
    try:
        rows = conn.execute(
            "SELECT title, decision_text, rationale, outcome_score, created_at "
            "FROM decisions WHERE workspace_id = ? "
            "AND status = 'active' AND outcome_score IS NOT NULL "
            "ORDER BY created_at ASC",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        return TrainReport(samples=0, trained=False, errors=[f"select:{exc}"])
    if len(rows) < min_samples():
        return TrainReport(samples=len(rows), trained=False)
    texts = [
        " ".join(str(r[i] or "") for i in range(3))  # title + decision_text + rationale
        for r in rows
    ]
    vocab = build_vocab(texts, size=VOCAB_SIZE)
    outcomes = [float(r[3] or 0.0) for r in rows]
    created_at_list = [str(r[4] or "") for r in rows]
    samples: list[tuple[list[float], int]] = []
    for i, (text, outcome) in enumerate(zip(texts, outcomes, strict=True)):
        recent = outcomes[max(0, i - 10) : i]
        # v3.4 #8: pull workspace audit signals AS OF this sample's
        # created_at so historical training samples never peek at
        # events that happened after them. Zeros on missing schema.
        audit_signals = workspace_audit_signals(
            conn, workspace_id=workspace_id, as_of_ts=created_at_list[i] or None
        )
        features = featurize(
            text=text,
            vocab=vocab,
            recent_outcomes=recent,
            trail_length=i,
            audit_signals=audit_signals,
        )
        label = 1 if outcome < -0.2 else 0
        samples.append((features, label))
    weights, bias, loss = _train_sgd(
        samples,
        dim=feature_dim(vocab, audit_dim=AUDIT_FEATURE_COUNT),
        n_epochs=epochs(),
        lr=learning_rate(),
    )
    model = LRModel(
        vocab=vocab,
        weights=weights,
        bias=bias,
        trained_at=now_iso,
        n_samples=len(samples),
        audit_dim=AUDIT_FEATURE_COUNT,
    )
    save_model(conn, workspace_id=workspace_id, model=model)
    return TrainReport(samples=len(samples), trained=True, final_loss=loss)
