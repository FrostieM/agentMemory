"""v3.1 Vector 1 MVP — heuristic experiment proposal."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.maintenance import experiment_proposal as ep

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_EXPERIMENT_PROPOSAL_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_EXPERIMENT_PROPOSAL_CONF_MIN", raising=False)
    monkeypatch.delenv("MEMORY_EXPERIMENT_PROPOSAL_CONF_MAX", raising=False)
    monkeypatch.delenv("MEMORY_EXPERIMENT_PROPOSAL_LIMIT", raising=False)
    # v3.1 LLM augmentation — OFF by default so heuristic-path tests
    # don't accidentally hit a dev-box Ollama (the LLM-specific tests
    # set the flag explicitly).
    monkeypatch.delenv("MEMORY_EXPERIMENT_PROPOSAL_LLM_ENABLED", raising=False)
    # v3.3 min-evidence gate — relax to 1 for legacy tests that seed
    # insights without explicit source_episode_ids. The min-evidence
    # behavior gets its own focused tests further down that re-set
    # the env to the production default (3).
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_MIN_EVIDENCE", "1")


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Real on-disk DB mirroring the production hybrid schema:

    * canonical ``0001_init.sql`` → ``insights`` table
    * legacy ``apply_migrations`` → ``memory_candidates`` table

    Production has both because the v3 cutover bolted canonical on top
    of legacy migrations. Tests need the same shape.
    """
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    db_path = tmp_path / "src.db"
    c = open_connection(db_path)
    # Legacy migrations first — creates memory_candidates, episodes, etc.
    apply_migrations(c)
    # Canonical schema overlay — adds the v3 ``insights`` table.
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _seed_episode(conn: sqlite3.Connection, *, ep_id: str, workspace_id: str = "ws") -> None:
    conn.execute(
        "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
        "VALUES (?, ?, 'agent_action', 'fixture episode', '2026-05-19T00:00:00+00:00')",
        (ep_id, workspace_id),
    )
    conn.commit()


def _seed_insight(
    conn: sqlite3.Connection,
    *,
    insight_id: str,
    summary: str,
    confidence: float,
    workspace_id: str = "ws",
    status: str = "candidate",
    source_episode_ids: list[str] | None = None,
) -> None:
    """Seed an insight. When ``source_episode_ids`` are given, seed the
    matching episode rows so the FK from memory_candidates (added by
    persist_proposal) resolves.

    v3.3: when caller passes nothing, default to a single auto-named
    episode so the min-evidence gate (floor=1 in the test fixture)
    doesn't drop every legacy seed silently. Tests that exercise the
    min-evidence behaviour pass an explicit list."""
    import json as _json  # noqa: PLC0415

    source_ids = (
        [f"ep_auto_{insight_id}"] if source_episode_ids is None else source_episode_ids
    )
    import contextlib  # noqa: PLC0415

    for ep_id in source_ids:
        # Idempotent insert — same episode id may be reused across insights.
        with contextlib.suppress(sqlite3.IntegrityError):
            _seed_episode(conn, ep_id=ep_id, workspace_id=workspace_id)
    conn.execute(
        """INSERT INTO insights
           (id, workspace_id, insight_type, summary, gist, proposed_action,
            target_type, target_id, source_episode_ids_json, confidence,
            status, tags_json, created_at, updated_at)
           VALUES (?, ?, 'lesson', ?, ?, NULL, NULL, NULL, ?, ?, ?,
                   '[]', '2026-05-19T00:00:00+00:00',
                   '2026-05-19T00:00:00+00:00')""",
        (
            insight_id,
            workspace_id,
            summary,
            summary,
            _json.dumps(source_ids),
            confidence,
            status,
        ),
    )
    conn.commit()


def test_returns_empty_when_disabled(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_ENABLED", "false")
    _seed_insight(conn, insight_id="ins_1", summary="generic body text", confidence=0.5)
    assert ep.find_proposal_candidates(conn, workspace_id="ws") == []


def test_returns_empty_when_no_insights(conn: sqlite3.Connection) -> None:
    assert ep.find_proposal_candidates(conn, workspace_id="ws") == []


def test_uncertain_insight_proposes(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Insight in (0.4, 0.7) confidence window → emits a proposal.

    Force-disable LLM augmentation so this test runs against the
    deterministic heuristic body even when Ollama is reachable
    (default LLM_ENABLED=True after 2026-05-20)."""
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_LLM_ENABLED", "false")
    _seed_insight(
        conn,
        insight_id="ins_kelly",
        summary="Quarter-Kelly bet sizing may improve drawdown",
        confidence=0.55,
    )
    out = ep.find_proposal_candidates(conn, workspace_id="ws")
    assert len(out) == 1
    assert out[0].insight_id == "ins_kelly"
    assert "Hypothesis" in out[0].proposal_text
    assert "Quarter-Kelly" in out[0].proposal_text
    assert out[0].validation_criterion
    assert out[0].confidence == pytest.approx(0.55)


def test_confidence_below_window_excluded(conn: sqlite3.Connection) -> None:
    """Insight with confidence < lo → NOT proposed (too weak to test)."""
    _seed_insight(conn, insight_id="ins_low", summary="generic body text", confidence=0.3)
    out = ep.find_proposal_candidates(conn, workspace_id="ws")
    assert out == []


def test_confidence_above_window_excluded(conn: sqlite3.Connection) -> None:
    """Insight with confidence > hi → already validated; no proposal needed."""
    _seed_insight(conn, insight_id="ins_high", summary="generic body text", confidence=0.9)
    out = ep.find_proposal_candidates(conn, workspace_id="ws")
    assert out == []


def test_accepted_insight_excluded(conn: sqlite3.Connection) -> None:
    """``status='accepted'`` insights are already validated → skip."""
    _seed_insight(
        conn,
        insight_id="ins_done",
        summary="generic body text",
        confidence=0.55,
        status="accepted",
    )
    out = ep.find_proposal_candidates(conn, workspace_id="ws")
    assert out == []


def test_workspace_isolation(conn: sqlite3.Connection) -> None:
    _seed_insight(
        conn,
        insight_id="ins_foreign",
        summary="generic body text",
        confidence=0.55,
        workspace_id="beta",
    )
    out = ep.find_proposal_candidates(conn, workspace_id="alpha")
    assert out == []


def test_limit_caps_results(conn: sqlite3.Connection) -> None:
    """Default limit + explicit override both bound the result count."""
    for i in range(5):
        _seed_insight(
            conn,
            insight_id=f"ins_{i}",
            summary=f"Topic number {i} with descriptive body",
            confidence=0.5 + i * 0.01,
        )
    out_default = ep.find_proposal_candidates(conn, workspace_id="ws")
    assert len(out_default) == 3  # default limit
    out_capped = ep.find_proposal_candidates(conn, workspace_id="ws", limit=2)
    assert len(out_capped) == 2


def test_ordered_by_confidence_desc(conn: sqlite3.Connection) -> None:
    """Higher-confidence insights come first (more likely to validate fast)."""
    _seed_insight(conn, insight_id="ins_low", summary="alpha body text", confidence=0.45)
    _seed_insight(conn, insight_id="ins_mid", summary="beta body text", confidence=0.55)
    _seed_insight(conn, insight_id="ins_high", summary="gamma body text", confidence=0.68)
    out = ep.find_proposal_candidates(conn, workspace_id="ws")
    ids = [p.insight_id for p in out]
    assert ids == ["ins_high", "ins_mid", "ins_low"]


def test_env_window_override(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Operator can tighten or widen the conf window via env."""
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_CONF_MIN", "0.6")
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_CONF_MAX", "0.65")
    _seed_insight(conn, insight_id="ins_just_in", summary="passes the gate", confidence=0.62)
    _seed_insight(conn, insight_id="ins_out", summary="below the gate", confidence=0.50)
    out = ep.find_proposal_candidates(conn, workspace_id="ws")
    ids = {p.insight_id for p in out}
    assert "ins_just_in" in ids
    assert "ins_out" not in ids


def test_env_helpers_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_EXPERIMENT_PROPOSAL_CONF_MIN", raising=False)
    monkeypatch.delenv("MEMORY_EXPERIMENT_PROPOSAL_CONF_MAX", raising=False)
    monkeypatch.delenv("MEMORY_EXPERIMENT_PROPOSAL_LIMIT", raising=False)
    lo, hi = ep.conf_window()
    assert lo == pytest.approx(0.4)
    assert hi == pytest.approx(0.7)
    assert ep.emit_limit() == 3


def test_env_invalid_values_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_CONF_MIN", "garbage")
    lo, _ = ep.conf_window()
    assert lo == pytest.approx(0.4)


# ============================================================
# persist_proposal — writes a memory_candidate row idempotently
# ============================================================


def test_persist_proposal_writes_candidate_row(conn: sqlite3.Connection) -> None:
    """persist_proposal lands a memory_candidate with kind='theory_proposal'."""
    _seed_episode(conn, ep_id="ep_seed")
    proposal = ep.ExperimentProposal(
        insight_id="ins_kelly",
        proposal_text="Hypothesis (from insight ins_kelly): Use quarter-Kelly",
        validation_criterion="Validate by 2+ episodes",
        confidence=0.55,
        source_episode_id="ep_seed",
    )
    candidate_id = ep.persist_proposal(conn, workspace_id="ws", proposal=proposal)
    assert candidate_id == "cand_prop_ins_kelly"
    row = conn.execute(
        "SELECT kind, subject, predicate, object, confidence, status "
        "FROM memory_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    assert row is not None
    assert row["kind"] == "theory_proposal"
    assert row["subject"] == "ins_kelly"
    assert row["predicate"] == "proposes_theory"
    assert "quarter-Kelly" in row["object"]
    assert row["confidence"] == pytest.approx(0.55)
    assert row["status"] == "new"


def test_persist_proposal_is_idempotent(conn: sqlite3.Connection) -> None:
    """Re-running on the same insight updates in place (no duplicate rows)."""
    _seed_episode(conn, ep_id="ep_idem")
    proposal_v1 = ep.ExperimentProposal(
        insight_id="ins_idem",
        proposal_text="v1 body",
        validation_criterion="v1 criterion",
        confidence=0.5,
        source_episode_id="ep_idem",
    )
    proposal_v2 = ep.ExperimentProposal(
        insight_id="ins_idem",
        proposal_text="v2 body (updated)",
        validation_criterion="v2 criterion",
        confidence=0.65,
        source_episode_id="ep_idem",
    )
    ep.persist_proposal(conn, workspace_id="ws", proposal=proposal_v1)
    ep.persist_proposal(conn, workspace_id="ws", proposal=proposal_v2)
    rows = conn.execute(
        "SELECT id, object, confidence FROM memory_candidates WHERE subject = 'ins_idem'"
    ).fetchall()
    assert len(rows) == 1
    assert "v2 body" in rows[0]["object"]
    assert rows[0]["confidence"] == pytest.approx(0.65)


def test_persist_proposal_metadata_marks_source(conn: sqlite3.Connection) -> None:
    """metadata_json records the proposal as MVP-sourced for traceability."""
    import json as _json  # noqa: PLC0415

    _seed_episode(conn, ep_id="ep_meta")
    proposal = ep.ExperimentProposal(
        insight_id="ins_meta",
        proposal_text="X",
        validation_criterion="Y",
        confidence=0.5,
        source_episode_id="ep_meta",
    )
    ep.persist_proposal(conn, workspace_id="ws", proposal=proposal)
    row = conn.execute(
        "SELECT metadata_json FROM memory_candidates WHERE subject = 'ins_meta'"
    ).fetchone()
    meta = _json.loads(row["metadata_json"])
    assert meta.get("source") == "experiment_proposal_mvp"


def test_persist_proposal_skips_when_no_source_episode(conn: sqlite3.Connection) -> None:
    """If the insight has no evidence episode, the FK can't resolve →
    skip persistence and return empty string rather than crashing."""
    proposal = ep.ExperimentProposal(
        insight_id="ins_orphan",
        proposal_text="X",
        validation_criterion="Y",
        confidence=0.5,
        source_episode_id="",  # explicitly empty
    )
    out = ep.persist_proposal(conn, workspace_id="ws", proposal=proposal)
    assert out == ""
    row = conn.execute(
        "SELECT COUNT(*) FROM memory_candidates WHERE subject = 'ins_orphan'"
    ).fetchone()
    assert row[0] == 0


def test_find_proposal_candidates_extracts_source_episode(conn: sqlite3.Connection) -> None:
    """``find_proposal_candidates`` populates source_episode_id from the
    insight's ``source_episode_ids_json`` first element so persist_proposal
    succeeds end-to-end."""
    _seed_insight(
        conn,
        insight_id="ins_with_evidence",
        summary="topic with descriptive body",
        confidence=0.55,
        source_episode_ids=["ep_first", "ep_second"],
    )
    proposals = ep.find_proposal_candidates(conn, workspace_id="ws")
    assert len(proposals) == 1
    assert proposals[0].source_episode_id == "ep_first"
    # Round-trip: persist then look up.
    candidate_id = ep.persist_proposal(conn, workspace_id="ws", proposal=proposals[0])
    assert candidate_id == "cand_prop_ins_with_evidence"


# ============================================================
# Vector1-audit coverage — new behaviour from audit rounds
# ============================================================


def test_min_summary_len_filters_whitespace_only(conn: sqlite3.Connection) -> None:
    """Vector1-audit M3: whitespace-only summary → filtered out, no
    "(no summary)" candidate lands."""
    _seed_insight(conn, insight_id="ins_blank", summary="   ", confidence=0.55)
    out = ep.find_proposal_candidates(conn, workspace_id="ws")
    assert out == []


def test_conf_window_sorts_inverted_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Vector1-audit M5: operator flips MIN > MAX → conf_window
    transparently sorts so the scanner stays well-defined."""
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_CONF_MIN", "0.8")
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_CONF_MAX", "0.4")
    lo, hi = ep.conf_window()
    assert lo == pytest.approx(0.4)
    assert hi == pytest.approx(0.8)


def test_first_source_episode_rejects_non_string_element() -> None:
    """Vector1-audit M6: a dict in source_episode_ids[0] returns "" so
    the FK explosion downstream is avoided."""
    from agent_memory_lite.maintenance.experiment_proposal import (  # noqa: PLC0415
        _first_source_episode,
    )

    assert _first_source_episode('[{"id": "ep_1"}, "ep_2"]') == ""
    assert _first_source_episode('[42, "ep_1"]') == ""
    assert _first_source_episode('[null, "ep_1"]') == ""
    # Valid string element still returns it.
    assert _first_source_episode('["ep_first", "ep_second"]') == "ep_first"


def test_find_proposal_candidates_propagates_missing_table(
    tmp_path: Path,
) -> None:
    """Vector1-audit H2: when the ``insights`` table is missing the
    scanner now lets the OperationalError propagate so the operator
    sees the misconfiguration instead of silently getting []."""
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415

    db_path = tmp_path / "no_insights.db"
    c = open_connection(db_path)
    try:
        # Don't apply migrations — ``insights`` table does not exist.
        with pytest.raises(sqlite3.OperationalError):
            ep.find_proposal_candidates(c, workspace_id="ws")
    finally:
        c.close()


def test_persist_proposal_preserves_promoted_status(conn: sqlite3.Connection) -> None:
    """Vector1-audit M1: re-persisting after operator promotion does NOT
    clobber the candidate body — ``WHERE status = 'new'`` makes the
    second write a no-op once the row leaves 'new'."""
    _seed_episode(conn, ep_id="ep_promo")
    proposal_v1 = ep.ExperimentProposal(
        insight_id="ins_promo",
        proposal_text="v1 body text",
        validation_criterion="v1 criterion",
        confidence=0.5,
        source_episode_id="ep_promo",
    )
    out1 = ep.persist_proposal(conn, workspace_id="ws", proposal=proposal_v1)
    assert out1 == "cand_prop_ins_promo"
    # Operator promotes the candidate manually.
    conn.execute(
        "UPDATE memory_candidates SET status = 'accepted' WHERE id = ?",
        (out1,),
    )
    conn.commit()
    # Brain pass re-runs with a (possibly different) v2 proposal.
    proposal_v2 = ep.ExperimentProposal(
        insight_id="ins_promo",
        proposal_text="v2 body (regenerated)",
        validation_criterion="v2 criterion",
        confidence=0.99,
        source_episode_id="ep_promo",
    )
    out2 = ep.persist_proposal(conn, workspace_id="ws", proposal=proposal_v2)
    # Skip return because status != 'new'.
    assert out2 == ""
    row = conn.execute(
        "SELECT object, confidence, status FROM memory_candidates WHERE id = ?",
        (out1,),
    ).fetchone()
    assert row["status"] == "accepted"
    assert "v1 body" in row["object"]
    assert row["confidence"] == pytest.approx(0.5)


def test_proposal_uses_llm_body_when_enabled(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM-augmented path: when the env flag is on AND the LLM returns
    two paragraphs, ``_proposal_from_insight`` adopts them verbatim
    instead of the heuristic formatting."""
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_LLM_ENABLED", "true")

    def _fake_llm_body(**_kwargs: object) -> tuple[str, str]:
        return (
            "Hypothesis: this is the LLM-generated body.",
            "Validation: surface 2+ confirming episodes within 30 days.",
        )

    from agent_memory_lite.maintenance import experiment_proposal as ep_mod  # noqa: PLC0415

    monkeypatch.setattr(
        "agent_memory_lite.maintenance.experiment_proposal_body.llm_body_for_insight",
        _fake_llm_body,
    )
    _seed_insight(
        conn,
        insight_id="ins_llm",
        summary="Quarter-Kelly bet sizing might improve drawdown",
        confidence=0.55,
    )
    proposals = ep_mod.find_proposal_candidates(conn, workspace_id="ws")
    assert len(proposals) == 1
    assert "LLM-generated body" in proposals[0].proposal_text
    assert "2+ confirming episodes" in proposals[0].validation_criterion
    # Heuristic preamble must NOT appear when LLM body is adopted.
    assert "(from insight ins_llm" not in proposals[0].proposal_text


def test_proposal_falls_back_to_heuristic_when_llm_returns_none(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM returning None (Ollama offline / parse error) → heuristic body."""
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_LLM_ENABLED", "true")
    # Patch the consumer's binding (experiment_proposal_body) — patching
    # the source module would be a no-op because body did a `from ...
    # import llm_body_for_insight` at module load, copying the
    # reference into its own namespace.
    monkeypatch.setattr(
        "agent_memory_lite.maintenance.experiment_proposal_body.llm_body_for_insight",
        lambda **_kwargs: None,
    )
    _seed_insight(
        conn,
        insight_id="ins_heur",
        summary="Quarter-Kelly bet sizing fallback path",
        confidence=0.55,
    )
    from agent_memory_lite.maintenance import experiment_proposal as ep_mod  # noqa: PLC0415

    proposals = ep_mod.find_proposal_candidates(conn, workspace_id="ws")
    assert len(proposals) == 1
    # Heuristic body signature.
    assert "Hypothesis (from insight ins_heur" in proposals[0].proposal_text


def test_persist_proposal_returns_empty_on_fk_violation(conn: sqlite3.Connection) -> None:
    """Vector1-audit H1: an FK violation (insight references a deleted
    episode) returns "" rather than crashing the batch."""
    # source_episode_id points to a row that doesn't exist in episodes.
    proposal = ep.ExperimentProposal(
        insight_id="ins_orphan_fk",
        proposal_text="body text valid",
        validation_criterion="criterion",
        confidence=0.55,
        source_episode_id="ep_nonexistent",
    )
    # FK enforcement: ensure FK pragma is on (it should be at app level
    # but we set it here defensively).
    conn.execute("PRAGMA foreign_keys = ON")
    out = ep.persist_proposal(conn, workspace_id="ws", proposal=proposal)
    assert out == ""
    row = conn.execute(
        "SELECT COUNT(*) FROM memory_candidates WHERE id = 'cand_prop_ins_orphan_fk'"
    ).fetchone()
    assert row[0] == 0


# ============================================================
# v3.2 reject-loop termination
# ============================================================


def _seed_triaged_candidate(
    conn: sqlite3.Connection,
    *,
    insight_id: str,
    status: str,
    workspace_id: str = "ws",
) -> None:
    """Helper: seed a memory_candidate(kind=theory_proposal) with the
    deterministic id + given status, mimicking what persist_proposal
    would write followed by an operator promote/reject."""
    cand_id = f"cand_prop_{insight_id}"
    ep_id = f"ep_{insight_id}"
    import contextlib  # noqa: PLC0415

    with contextlib.suppress(sqlite3.IntegrityError):
        _seed_episode(conn, ep_id=ep_id, workspace_id=workspace_id)
    conn.execute(
        """INSERT INTO memory_candidates (
            id, workspace_id, kind, subject, predicate, object, evidence,
            confidence, importance, trust_level, temporal_json,
            write_targets_json, metadata_json, source_episode_id,
            status, created_at, updated_at
        ) VALUES (?, ?, 'theory_proposal', ?, 'proposes_theory', ?, '',
                  0.55, 0.5, 'agent_observed', '{}', '[]', '{}', ?, ?,
                  '2026-05-19T00:00:00+00:00',
                  '2026-05-19T00:00:00+00:00')""",
        (cand_id, workspace_id, insight_id, "prior body", ep_id, status),
    )
    conn.commit()


def test_rejected_insight_is_skipped(conn: sqlite3.Connection) -> None:
    """v3.2 reject-loop termination: an insight whose candidate is
    rejected does NOT come back on the next scan — saves the LLM call
    AND stops the UI bounce-back where the operator sees a rejected
    proposal reappear."""
    _seed_insight(
        conn,
        insight_id="ins_already_rejected",
        summary="Some hypothesis that was rejected before",
        confidence=0.55,
        source_episode_ids=["ep_ins_already_rejected"],
    )
    _seed_triaged_candidate(conn, insight_id="ins_already_rejected", status="rejected")

    proposals = ep.find_proposal_candidates(conn, workspace_id="ws")
    insight_ids = {p.insight_id for p in proposals}
    assert "ins_already_rejected" not in insight_ids


def test_accepted_insight_is_skipped(conn: sqlite3.Connection) -> None:
    """An accepted candidate also blocks regeneration. The accepted
    proposal already lives in the theories table — no need to surface
    a parallel proposal of the same hypothesis."""
    _seed_insight(
        conn,
        insight_id="ins_already_accepted",
        summary="Hypothesis promoted to a theory",
        confidence=0.6,
        source_episode_ids=["ep_ins_already_accepted"],
    )
    _seed_triaged_candidate(conn, insight_id="ins_already_accepted", status="accepted")

    proposals = ep.find_proposal_candidates(conn, workspace_id="ws")
    insight_ids = {p.insight_id for p in proposals}
    assert "ins_already_accepted" not in insight_ids


def test_new_status_candidate_does_not_block(conn: sqlite3.Connection) -> None:
    """A candidate still in 'new' state should NOT block its insight —
    the persist layer's ON CONFLICT UPDATE will refresh the body, so the
    scanner should still consider it."""
    _seed_insight(
        conn,
        insight_id="ins_pending_review",
        summary="Hypothesis pending operator triage",
        confidence=0.5,
        source_episode_ids=["ep_ins_pending_review"],
    )
    _seed_triaged_candidate(conn, insight_id="ins_pending_review", status="new")

    proposals = ep.find_proposal_candidates(conn, workspace_id="ws")
    insight_ids = {p.insight_id for p in proposals}
    assert "ins_pending_review" in insight_ids


def test_workspace_isolation_for_rejected_filter(conn: sqlite3.Connection) -> None:
    """A rejected candidate in workspace A must not block scanning of
    the same insight_id in workspace B."""
    _seed_insight(
        conn,
        insight_id="ins_xws",
        summary="cross-workspace shared insight id",
        confidence=0.5,
        source_episode_ids=["ep_ws_b"],
        workspace_id="ws_b",
    )
    _seed_triaged_candidate(conn, insight_id="ins_xws", status="rejected", workspace_id="ws_a")

    proposals_a = ep.find_proposal_candidates(conn, workspace_id="ws_a")
    proposals_b = ep.find_proposal_candidates(conn, workspace_id="ws_b")
    assert "ins_xws" not in {p.insight_id for p in proposals_a}
    assert "ins_xws" in {p.insight_id for p in proposals_b}


# ============================================================
# v3.3 min-evidence gate
# ============================================================


def test_min_evidence_gate_drops_thin_insight(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v3.3: an insight backed by fewer than min_evidence episodes is
    skipped. Default min_evidence=3 (per roadmap)."""
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_MIN_EVIDENCE", "3")
    _seed_insight(
        conn,
        insight_id="ins_thin",
        summary="Hypothesis backed by 1 episode only",
        confidence=0.55,
        source_episode_ids=["ep_only"],
    )
    out = ep.find_proposal_candidates(conn, workspace_id="ws")
    assert "ins_thin" not in {p.insight_id for p in out}


def test_min_evidence_gate_passes_thick_insight(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Insight with >= min_evidence episodes survives the gate."""
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_MIN_EVIDENCE", "3")
    _seed_insight(
        conn,
        insight_id="ins_thick",
        summary="Hypothesis with three pieces of evidence",
        confidence=0.55,
        source_episode_ids=["e1", "e2", "e3"],
    )
    out = ep.find_proposal_candidates(conn, workspace_id="ws")
    assert "ins_thick" in {p.insight_id for p in out}


def test_min_evidence_default_is_three(monkeypatch: pytest.MonkeyPatch) -> None:
    """Roadmap default — operator must explicitly lower for testing."""
    monkeypatch.delenv("MEMORY_EXPERIMENT_PROPOSAL_MIN_EVIDENCE", raising=False)
    from agent_memory_lite.maintenance.experiment_proposal_config import (  # noqa: PLC0415
        min_evidence,
    )

    assert min_evidence() == 3


def test_min_evidence_floor_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bogus value below floor → fall back to floor=1."""
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_MIN_EVIDENCE", "-5")
    from agent_memory_lite.maintenance.experiment_proposal_config import (  # noqa: PLC0415
        min_evidence,
    )

    assert min_evidence() == 1


def test_evidence_count_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    """The internal counter handles malformed JSON gracefully."""
    from agent_memory_lite.maintenance import experiment_proposal as mod  # noqa: PLC0415

    assert mod._evidence_count(None) == 0
    assert mod._evidence_count("") == 0
    assert mod._evidence_count('["e1", "e2", "e3"]') == 3
    assert mod._evidence_count('["e1", null, "e2"]') == 2  # nulls don't count
    assert mod._evidence_count('["e1", {"id": "e2"}, "e3"]') == 2  # dicts don't count
    assert mod._evidence_count('"not a list"') == 0
    assert mod._evidence_count("malformed json") == 0


# ============================================================
# v3.3 LLM generic-noise filter
# ============================================================


def test_looks_like_generic_llm_noise_catches_ai_agent_opener() -> None:
    from agent_memory_lite.maintenance.experiment_proposal_body import (  # noqa: PLC0415
        looks_like_generic_llm_noise,
    )

    assert looks_like_generic_llm_noise(
        "AI agent exhibits bias in decision-making processes when allocating"
    )
    assert looks_like_generic_llm_noise(
        "The AI agent's performance improves incrementally when processing"
    )
    assert looks_like_generic_llm_noise("The agent demonstrates superior accuracy")
    assert looks_like_generic_llm_noise(
        "HYPOTHESIS: The AI agent exhibits a tendency to..."
    )


def test_looks_like_generic_llm_noise_passes_real_hypothesis() -> None:
    from agent_memory_lite.maintenance.experiment_proposal_body import (  # noqa: PLC0415
        looks_like_generic_llm_noise,
    )

    # Real domain-specific hypotheses start with the substrate, not the actor.
    assert not looks_like_generic_llm_noise(
        "Quarter-Kelly bet sizing improves drawdown compared to half-Kelly"
    )
    assert not looks_like_generic_llm_noise(
        "Boot-gate filter rejects param_kind=exit before strategy runs"
    )
    assert not looks_like_generic_llm_noise(
        "Hypothesis: cross-file resolver finds 95% of symbol refs without grep"
    )


def test_looks_like_generic_llm_noise_handles_empty() -> None:
    from agent_memory_lite.maintenance.experiment_proposal_body import (  # noqa: PLC0415
        looks_like_generic_llm_noise,
    )

    assert looks_like_generic_llm_noise("")
    assert looks_like_generic_llm_noise("   ")


def test_try_llm_body_filters_generic_noise(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the LLM returns a generic 'AI agent exhibits...' body, the
    try_llm_body wrapper drops it so the caller falls back to the
    heuristic body (which at least quotes the source insight)."""
    from agent_memory_lite.maintenance import experiment_proposal_body as body_mod  # noqa: PLC0415

    monkeypatch.setattr(
        body_mod,
        "llm_body_for_insight",
        lambda **_kw: (
            "AI agent exhibits bias in decision-making processes when calibrating",
            "Validation: the agent consistently underperforms across multiple tests",
        ),
    )
    out = body_mod.try_llm_body("ins_x", "kelly sizing maybe improves drawdown", 0.55)
    assert out is None  # filter dropped the generic body


def test_try_llm_body_passes_real_hypothesis(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuine domain-specific LLM body passes through unchanged."""
    from agent_memory_lite.maintenance import experiment_proposal_body as body_mod  # noqa: PLC0415

    good_body = (
        "Quarter-Kelly bet sizing reduces 95th percentile drawdown by >=30% "
        "vs half-Kelly across 500 simulated days.",
        "Validate by replaying 500 days at both fractions and comparing drawdown.",
    )
    monkeypatch.setattr(body_mod, "llm_body_for_insight", lambda **_kw: good_body)
    out = body_mod.try_llm_body("ins_y", "kelly sizing improves drawdown", 0.55)
    assert out == good_body


def test_excluded_helper_failure_soft_no_table(tmp_path: Path) -> None:
    """If neither candidates table exists (pre-migration DB), the helper
    returns empty set so the scanner still runs on insights alone."""
    db = tmp_path / "bare.db"
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    # Only create insights table — no memory_candidates / candidates.
    c.executescript("""
        CREATE TABLE insights (
            id TEXT PRIMARY KEY, workspace_id TEXT, status TEXT,
            confidence REAL, summary TEXT, gist TEXT,
            source_episode_ids_json TEXT
        );
    """)
    try:
        result = ep._excluded_insight_ids(c, workspace_id="ws")
        assert result == set()
    finally:
        c.close()
