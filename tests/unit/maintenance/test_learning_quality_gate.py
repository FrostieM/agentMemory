"""Batch H: learning-quality gate.

The per-loop unit tests prove each cognition step runs; the failure-escalation
suite proves a DEAD step turns /health red. This gate proves the learning is
DIRECTIONALLY CORRECT over a representative corpus -- the actual "quality": the
feedback -> EWMA -> outcome loop must rank a strongly-useful decision ABOVE a
strongly-useless one, and the core loops must all fire together without error.

A loop that silently inverts, flattens, or stops discriminating -- a quality
regression a "did it run" test would miss -- turns this red.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.maintenance.brain_pass import run_brain_pass
from agent_memory_lite.utils.time import iso_now

_WS = "ws"


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


def _seed_decision(c: sqlite3.Connection, *, id_: str) -> None:
    c.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at, outcome_score, pinned, feedback_ewma, last_retrieved_at)
           VALUES (?, ?, ?, 'body', 'g', 'active', ?, ?, ?, 0.0, 0, 0.0, ?)""",
        (id_, _WS, id_, iso_now(), iso_now(), iso_now(), iso_now()),
    )
    c.commit()


def _seed_feedback(c: sqlite3.Connection, *, source_id: str, usefulness: float, n: int = 3) -> None:
    for i in range(n):
        c.execute(
            "INSERT INTO memory_usage_feedback "
            "(id, workspace_id, source_type, source_id, query, usefulness, notes, created_at, source) "
            "VALUES (?, ?, 'decision', ?, '', ?, '', ?, 'agent_observed')",
            (f"fb_{source_id}_{i}", _WS, source_id, usefulness, iso_now()),
        )
    c.commit()


def _outcome(c: sqlite3.Connection, id_: str) -> float:
    return float(
        c.execute("SELECT outcome_score FROM decisions WHERE id = ?", (id_,)).fetchone()[0]
    )


def test_learning_discriminates_useful_from_useless(conn: sqlite3.Connection) -> None:
    """Directional quality: useful feedback drives outcome POSITIVE and harmful
    feedback drives it NEGATIVE after one brain pass.

    M5 (global-audit 2026-06-30): this previously seeded usefulness=0.05 for the
    "useless" decision, but usefulness is on a [-1, 1] scale (0 = neutral), so 0.05
    is POSITIVE -- the test only ranked two positive values and never proved the
    loop maps harmful feedback to a NEGATIVE outcome (the signal that lets a bad
    memory be discredited). It now seeds genuinely-harmful feedback and asserts the
    sign."""
    _seed_decision(conn, id_="dec_useful")
    _seed_decision(conn, id_="dec_useless")
    _seed_feedback(conn, source_id="dec_useful", usefulness=0.95)
    _seed_feedback(conn, source_id="dec_useless", usefulness=-0.9)

    report = run_brain_pass(conn, workspace_id=_WS, settings=Settings())
    assert report.errors == []
    assert report.feedback_ewma_recomputed >= 2
    assert "decision" in report.outcome_updated

    useful, useless = _outcome(conn, "dec_useful"), _outcome(conn, "dec_useless")
    # RANK correctly AND get the signs right: useful positive, harmful negative.
    assert useful > useless, f"learning failed to discriminate: useful={useful} useless={useless}"
    assert useful > 0.0, f"useful feedback did not drive a positive outcome: {useful}"
    assert useless < 0.0, f"harmful feedback did not drive a NEGATIVE outcome: {useless}"


def test_core_learning_loops_all_fire_without_error(conn: sqlite3.Connection) -> None:
    """Liveness canary: over a seeded corpus the core loops (feedback EWMA ->
    outcome, self-model) all fire together and the pass records zero errors. A
    silently-dead core loop -- the failure this subsystem exists to prevent --
    breaks this."""
    _seed_decision(conn, id_="dec_live")
    _seed_feedback(conn, source_id="dec_live", usefulness=0.9)

    report = run_brain_pass(conn, workspace_id=_WS, settings=Settings())
    assert report.errors == []
    assert report.feedback_ewma_recomputed >= 1  # feedback loop fired
    assert "decision" in report.outcome_updated  # outcome loop fired
    assert report.self_model_refreshed is True  # self-model loop fired
    # predictive-LR is wired (its step ran; samples reported, no error above).
    assert report.predictive_lr_samples >= 0
