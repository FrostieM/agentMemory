"""v3.4 #1 — closed-loop autonomous research agent tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.cognition.autonomous_loop import (
    _score_candidate,
    _synthesize_discipline,
    confidence_threshold,
    is_enabled,
    max_promotions_per_pass,
    run_autonomous_pass,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_AUTONOMOUS_LOOP_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_AUTONOMOUS_LOOP_CONFIDENCE_THRESHOLD", raising=False)
    monkeypatch.delenv("MEMORY_AUTONOMOUS_LOOP_MAX_PROMOTIONS", raising=False)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Hybrid schema (legacy + canonical) so candidates,
    theories, theory_evidence, episodes, insights all exist."""
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    c = open_connection(tmp_path / "loop.db")
    apply_migrations(c)
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def _seed_episode(conn: sqlite3.Connection, *, ep_id: str, text: str, ws: str = "ws") -> None:
    conn.execute(
        "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
        "VALUES (?, ?, 'agent_action', ?, '2026-05-19T00:00:00+00:00')",
        (ep_id, ws, text),
    )
    conn.commit()


def _seed_insight(
    conn: sqlite3.Connection,
    *,
    insight_id: str,
    summary: str,
    source_eps: list[str],
    ws: str = "ws",
) -> None:
    import json as _j  # noqa: PLC0415

    conn.execute(
        """INSERT INTO insights
           (id, workspace_id, insight_type, summary, gist, status,
            source_episode_ids_json, confidence, tags_json,
            created_at, updated_at)
           VALUES (?, ?, 'lesson', ?, ?, 'candidate', ?, 0.55, '[]',
                   '2026-05-19T00:00:00+00:00', '2026-05-19T00:00:00+00:00')""",
        (insight_id, ws, summary, summary, _j.dumps(source_eps)),
    )
    conn.commit()


def _seed_candidate(
    conn: sqlite3.Connection,
    *,
    cand_id: str,
    insight_id: str,
    proposal_text: str,
    primary_ep: str = "ep_seed",
    ws: str = "ws",
) -> None:
    conn.execute(
        """INSERT INTO candidates
           (id, workspace_id, kind, subject, predicate, object, evidence,
            confidence, importance, trust_level, temporal_json,
            write_targets_json, metadata_json, source_episode_id, status,
            created_at, updated_at)
           VALUES (?, ?, 'theory_proposal', ?, 'proposes_theory', ?, '',
                   0.55, 0.5, 'agent_observed', '{}', '[]', '{}', ?, 'new',
                   '2026-05-19T00:00:00+00:00', '2026-05-19T00:00:00+00:00')""",
        (cand_id, ws, insight_id, proposal_text, primary_ep),
    )
    conn.commit()


def test_defaults() -> None:
    assert is_enabled() is True
    assert confidence_threshold() == pytest.approx(0.6)
    assert max_promotions_per_pass() == 3


def test_score_candidate_strong_corroboration() -> None:
    """3 source episodes + 2 more episode corpus matches → confidence
    crosses threshold."""
    proposal = "Quarter-Kelly bet sizing improves drawdown across replays"
    src_eps = ["ep1", "ep2", "ep3"]
    corpus = [
        ("ep4", "another quarter-Kelly drawdown experiment showed improvement"),
        ("ep5", "kelly drawdown test in tier 2"),
        ("ep6", "unrelated polymarket maker pricing"),
    ]
    conf, supporting = _score_candidate(
        proposal_text=proposal, source_episode_ids=src_eps, episode_corpus=corpus
    )
    # 3 sources + 2 matched = 5 supporting → 0.3 + 0.5 = 0.8.
    assert "ep4" in supporting
    assert "ep5" in supporting
    assert "ep6" not in supporting
    assert conf == pytest.approx(0.8)


def test_score_candidate_no_evidence() -> None:
    """Empty proposal → 0 confidence."""
    conf, supporting = _score_candidate(proposal_text="", source_episode_ids=[], episode_corpus=[])
    assert conf == 0.0
    assert supporting == []


def test_synthesize_discipline_returns_non_empty_lists() -> None:
    """Both lists must be non-empty for every input — the audit hygiene
    check classifies a theory as undisciplined the moment either list is
    empty, so the helper has no input range where it may return ``[]``."""
    preds, vcs = _synthesize_discipline(
        proposal_text="Quarter-Kelly bet sizing improves drawdown",
        supporting_episode_count=3,
    )
    assert len(preds) >= 1
    assert len(vcs) >= 1
    assert all(isinstance(s, str) and s.strip() for s in preds + vcs)
    # The starting evidence count must surface in the prediction body so
    # the next audit can measure growth deterministically.
    assert "3" in preds[0]


def test_synthesize_discipline_truncates_long_proposal() -> None:
    """A wall-of-text proposal must not blow up the validation_criterion
    body — we cap the preview at 140 chars + ellipsis."""
    long_text = "Kelly sizing " * 80  # ~1040 chars
    preds, vcs = _synthesize_discipline(proposal_text=long_text, supporting_episode_count=0)
    # The validation criterion embeds the truncated preview.
    assert "..." in vcs[0]
    # Prediction count is still solid: starting evidence count makes it in.
    assert "0" in preds[0]


def test_synthesize_discipline_handles_blank_proposal() -> None:
    """Empty/whitespace proposal still produces a usable validation
    criterion (the surrounding contract guarantees non-empty lists)."""
    preds, vcs = _synthesize_discipline(proposal_text="   ", supporting_episode_count=5)
    assert len(preds) >= 1
    assert len(vcs) >= 1
    assert all(s.strip() for s in preds + vcs)


def test_run_autonomous_pass_promotes_confident_candidate(
    conn: sqlite3.Connection,
) -> None:
    """Candidate with 3 source eps + 1 corpus match → confidence 0.7 →
    promoted to theory + 4 theory_evidence rows."""
    for ep_id in ("ep1", "ep2", "ep3"):
        _seed_episode(conn, ep_id=ep_id, text="kelly drawdown quarter")
    _seed_episode(conn, ep_id="ep4", text="kelly drawdown additional run")
    _seed_insight(
        conn,
        insight_id="ins_kelly",
        summary="Quarter-Kelly sizing reduces drawdown",
        source_eps=["ep1", "ep2", "ep3"],
    )
    _seed_candidate(
        conn,
        cand_id="cand_prop_ins_kelly",
        insight_id="ins_kelly",
        proposal_text="Quarter-Kelly bet sizing improves drawdown",
        primary_ep="ep1",
    )
    report = run_autonomous_pass(conn, workspace_id="ws")
    assert report.examined == 1
    assert report.promoted == 1
    assert report.held == 0
    # Theory row was written.
    theories = conn.execute(
        "SELECT id, claim, confidence, status, predictions_json,"
        " validation_criteria_json FROM theories WHERE workspace_id = 'ws'"
    ).fetchall()
    assert len(theories) == 1
    assert theories[0][3] == "proposed"
    # v3.4 audit-followup: every autonomously-promoted theory must carry
    # non-empty discipline fields so memory_audit.py never flags it as
    # undisciplined. Both fields are JSON-encoded lists of strings.
    import json as _j  # noqa: PLC0415

    preds = _j.loads(theories[0][4] or "[]")
    vcs = _j.loads(theories[0][5] or "[]")
    assert len(preds) >= 1, "autonomously-promoted theory must have >=1 prediction"
    assert len(vcs) >= 1, "autonomously-promoted theory must have >=1 validation_criterion"
    assert all(isinstance(p, str) and p.strip() for p in preds)
    assert all(isinstance(v, str) and v.strip() for v in vcs)
    # theory_evidence rows: at minimum the 3 source episodes + 1 corpus = 4.
    n_ev = conn.execute(
        "SELECT COUNT(*) FROM theory_evidence WHERE theory_id = ?",
        (theories[0][0],),
    ).fetchone()[0]
    assert n_ev == 4
    # Candidate flipped to accepted.
    cand = conn.execute("SELECT status FROM candidates WHERE id = 'cand_prop_ins_kelly'").fetchone()
    assert cand[0] == "accepted"


def test_run_autonomous_pass_holds_weak_candidate(
    conn: sqlite3.Connection,
) -> None:
    """1 source episode + 0 corpus match → confidence 0.4 < 0.6 → hold."""
    _seed_episode(conn, ep_id="ep_solo", text="kelly")
    _seed_insight(
        conn,
        insight_id="ins_weak",
        summary="weak signal",
        source_eps=["ep_solo"],
    )
    _seed_candidate(
        conn,
        cand_id="cand_prop_ins_weak",
        insight_id="ins_weak",
        proposal_text="weak proposal with thin evidence",
        primary_ep="ep_solo",
    )
    report = run_autonomous_pass(conn, workspace_id="ws")
    assert report.examined == 1
    assert report.promoted == 0
    assert report.held == 1
    # Candidate stays new.
    cand = conn.execute("SELECT status FROM candidates WHERE id = 'cand_prop_ins_weak'").fetchone()
    assert cand[0] == "new"
    # No theory written.
    n_theories = conn.execute("SELECT COUNT(*) FROM theories WHERE workspace_id = 'ws'").fetchone()[
        0
    ]
    assert n_theories == 0


def test_disabled_short_circuits(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_AUTONOMOUS_LOOP_ENABLED", "false")
    for ep_id in ("ep1", "ep2", "ep3"):
        _seed_episode(conn, ep_id=ep_id, text="kelly drawdown")
    _seed_insight(conn, insight_id="ins_x", summary="x", source_eps=["ep1", "ep2", "ep3"])
    _seed_candidate(
        conn,
        cand_id="cand_prop_ins_x",
        insight_id="ins_x",
        proposal_text="kelly drawdown improves",
        primary_ep="ep1",
    )
    report = run_autonomous_pass(conn, workspace_id="ws")
    assert report.examined == 0
    assert report.promoted == 0


def test_max_promotions_per_pass_cap(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cap=2 with 5 confident candidates → only 2 promoted this pass.
    The other 3 stay 'new' for next pass."""
    monkeypatch.setenv("MEMORY_AUTONOMOUS_LOOP_MAX_PROMOTIONS", "2")
    for i in range(5):
        for j in range(3):
            _seed_episode(conn, ep_id=f"ep_{i}_{j}", text=f"kelly drawdown shared term {i}")
        _seed_insight(
            conn,
            insight_id=f"ins_{i}",
            summary=f"hypothesis {i}",
            source_eps=[f"ep_{i}_0", f"ep_{i}_1", f"ep_{i}_2"],
        )
        _seed_candidate(
            conn,
            cand_id=f"cand_prop_ins_{i}",
            insight_id=f"ins_{i}",
            proposal_text=f"kelly drawdown improves variant {i}",
            primary_ep=f"ep_{i}_0",
        )
    report = run_autonomous_pass(conn, workspace_id="ws")
    assert report.promoted == 2
    # Total candidates examined ≤ first-cap-hit + 1 (loop breaks AFTER promote).
    assert report.examined >= 2


def test_workspace_isolation(conn: sqlite3.Connection) -> None:
    """Candidate in workspace A is invisible to autonomous pass in B."""
    for ep_id in ("ep1", "ep2", "ep3"):
        _seed_episode(conn, ep_id=ep_id, text="kelly drawdown", ws="ws_a")
    _seed_insight(
        conn,
        insight_id="ins_a",
        summary="a",
        source_eps=["ep1", "ep2", "ep3"],
        ws="ws_a",
    )
    _seed_candidate(
        conn,
        cand_id="cand_prop_ins_a",
        insight_id="ins_a",
        proposal_text="kelly drawdown improves",
        primary_ep="ep1",
        ws="ws_a",
    )
    report = run_autonomous_pass(conn, workspace_id="ws_b")
    assert report.examined == 0


def test_failure_soft_missing_tables(tmp_path: Path) -> None:
    """Pre-migration DB → empty report, no exception."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    try:
        report = run_autonomous_pass(c, workspace_id="ws")
        assert report.examined == 0
        assert report.errors == []
    finally:
        c.close()


def test_idempotent_rerun(conn: sqlite3.Connection) -> None:
    """Re-running the pass after a successful promotion does nothing
    new — accepted candidates are excluded from the scan."""
    for ep_id in ("ep1", "ep2", "ep3", "ep4"):
        _seed_episode(conn, ep_id=ep_id, text="kelly drawdown shared")
    _seed_insight(
        conn,
        insight_id="ins_done",
        summary="will promote",
        source_eps=["ep1", "ep2", "ep3"],
    )
    _seed_candidate(
        conn,
        cand_id="cand_prop_ins_done",
        insight_id="ins_done",
        proposal_text="kelly drawdown improves with extra evidence",
        primary_ep="ep1",
    )
    r1 = run_autonomous_pass(conn, workspace_id="ws")
    assert r1.promoted == 1
    r2 = run_autonomous_pass(conn, workspace_id="ws")
    assert r2.examined == 0  # accepted candidate filtered out
    assert r2.promoted == 0
