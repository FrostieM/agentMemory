"""Phase 3: consolidation feedback -- insights, episode boost, behavior detect."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.cognition.consolidation import (
    Cluster,
    EpisodeView,
    _matches_pinned_behavior,
    _pinned_behavior_token_sets,
    consolidate_workspace,
    distill_cluster,
)
from agent_memory_lite.utils.time import iso_now

CONSOL_PATH = (
    Path(__file__).resolve().parents[3]
    / "migrations"
    / "canonical"
    / "0004_consolidation_feedback.sql"
)


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


def _seed_episode(conn: sqlite3.Connection, *, id_: str, raw_text: str) -> None:
    conn.execute(
        """INSERT INTO episodes
           (id, workspace_id, source_type, raw_text, gist, created_at, is_archived)
           VALUES (?, 'ws', 'agent_action', ?, ?, ?, 0)""",
        (id_, raw_text, raw_text[:60], iso_now()),
    )
    conn.commit()


def _seed_pinned_behavior(conn: sqlite3.Connection, *, id_: str, rule: str) -> None:
    conn.execute(
        """INSERT INTO behaviors
           (id, workspace_id, name, kind, scope, priority, rule, rule_one_line,
            applies_to_json, active, pinned, created_at, updated_at)
           VALUES (?, 'ws', ?, 'operating_rule', 'workspace', 'project_convention',
                   ?, ?, '[]', 1, 1, ?, ?)""",
        (id_, id_, rule, rule[:120], iso_now(), iso_now()),
    )
    conn.commit()


@pytest.fixture
def llm_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force consolidation's LLM path to return a real 'Pattern:' summary.

    v3.7: consolidate_workspace skips persisting an insight whose summary
    is the word-frequency heuristic fallback. Tests that need an insight
    to land patch the LLM distiller to return a non-heuristic summary.
    """
    monkeypatch.setattr(
        "agent_memory_lite.cognition.consolidation_llm.llm_distill_cluster",
        lambda **_kw: "Pattern: a real distilled consolidation insight.",
    )
    monkeypatch.setenv("MEMORY_CONSOLIDATION_LLM_ENABLED", "true")


# ============================================================
# evidence-episode feedback boost
# ============================================================


def test_consolidation_writes_implicit_feedback_for_each_episode(
    conn: sqlite3.Connection, llm_pattern: None
) -> None:
    """4 episodes with shared tokens -> 1 cluster -> 1 insight -> 4 feedback rows."""
    for i in range(4):
        _seed_episode(conn, id_=f"ep_{i}", raw_text="kelly sizing failure divergence experiment")
    report = consolidate_workspace(conn, workspace_id="ws", window_hours=24)
    assert report.insights_written == 1
    fb_rows = conn.execute(
        "SELECT source_type, source_id, usefulness FROM memory_usage_feedback "
        "WHERE workspace_id = 'ws' AND source_type = 'episode'"
    ).fetchall()
    # Each evidence episode gets one feedback row.
    assert len(fb_rows) == 4
    for row in fb_rows:
        assert row["source_type"] == "episode"
        assert row["usefulness"] == pytest.approx(0.4, abs=1e-3)


def test_consolidation_emits_one_insight(conn: sqlite3.Connection, llm_pattern: None) -> None:
    for i in range(3):
        _seed_episode(conn, id_=f"ep_{i}", raw_text="calibrator volatility threshold tuning")
    consolidate_workspace(conn, workspace_id="ws")
    rows = conn.execute(
        "SELECT insight_type, status FROM insights WHERE workspace_id = 'ws'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "candidate"


# ============================================================
# behavior_reinforcement detection
# ============================================================


def test_matches_pinned_behavior_jaccard_threshold() -> None:
    behavior_tokens = [frozenset({"kelly", "sizing", "quarter", "cap"})]
    matching = {"kelly", "sizing", "quarter"}  # 3/4 = 0.75
    not_matching = {"orderbook", "rebalance"}
    assert _matches_pinned_behavior(matching, behavior_tokens, threshold=0.6)
    assert not _matches_pinned_behavior(not_matching, behavior_tokens, threshold=0.6)


def test_cluster_matching_pinned_behavior_gets_reinforcement_type(
    conn: sqlite3.Connection, llm_pattern: None
) -> None:
    # Overlap needs Jaccard >= 0.6 between cluster signal_tokens and behavior
    # tokens. Use a tight, low-noise rule so all 3+ shared tokens dominate the
    # union.
    _seed_pinned_behavior(
        conn,
        id_="beh_kelly",
        rule="kelly sizing capped quarter",
    )
    for i in range(3):
        _seed_episode(
            conn,
            id_=f"ep_{i}",
            raw_text="kelly sizing capped quarter",
        )
    consolidate_workspace(conn, workspace_id="ws")
    row = conn.execute(
        "SELECT insight_type FROM insights WHERE workspace_id = 'ws' LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["insight_type"] == "behavior_reinforcement"


def test_cluster_not_matching_behavior_gets_consolidation_type(
    conn: sqlite3.Connection, llm_pattern: None
) -> None:
    _seed_pinned_behavior(conn, id_="beh_kelly", rule="kelly sizing capped at quarter")
    for i in range(3):
        _seed_episode(conn, id_=f"ep_{i}", raw_text="orderbook depth stability monitoring latency")
    consolidate_workspace(conn, workspace_id="ws")
    row = conn.execute(
        "SELECT insight_type FROM insights WHERE workspace_id = 'ws' LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["insight_type"] == "consolidation"


# ============================================================
# pre-migration compatibility
# ============================================================


def test_pinned_behavior_tokens_empty_on_empty_workspace(conn: sqlite3.Connection) -> None:
    """No pinned behaviors -> empty list (not an error)."""
    assert _pinned_behavior_token_sets(conn, workspace_id="ws") == []


def test_distill_cluster_returns_summary_and_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    """v3.2: force LLM off to lock heuristic body shape — the LLM path
    has its own tests in test_consolidation_llm.py."""
    monkeypatch.setenv("MEMORY_CONSOLIDATION_LLM_ENABLED", "false")
    episodes = [
        EpisodeView(
            id="e1", gist="kelly sizing one", ts="t1", token_set=frozenset({"kelly", "sizing"})
        ),
        EpisodeView(
            id="e2", gist="kelly sizing two", ts="t2", token_set=frozenset({"kelly", "sizing"})
        ),
    ]
    cluster = Cluster(seed=episodes[0], members=episodes)
    draft = distill_cluster(cluster)
    assert "kelly" in draft.summary
    assert draft.evidence_episode_ids == ["e1", "e2"]


def test_distill_cluster_uses_llm_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """v3.2: when consolidation_llm.llm_distill_cluster returns a
    non-empty body, distill_cluster surfaces it instead of the
    'Recurring theme (N episodes): tok1, tok2' fallback."""
    from agent_memory_lite.cognition import consolidation as cmod  # noqa: PLC0415

    def fake_llm(*, excerpts: list[str], **_kw: object) -> str:
        return "Pattern: agent kept reaching for kelly sizing in identical setups."

    monkeypatch.setattr(
        "agent_memory_lite.cognition.consolidation_llm.llm_distill_cluster", fake_llm
    )
    # Force the inner _try_llm_summary to think LLM is enabled regardless of env.
    monkeypatch.setenv("MEMORY_CONSOLIDATION_LLM_ENABLED", "true")

    episodes = [
        EpisodeView(
            id=f"e{i}",
            gist=f"kelly sizing experiment {i}",
            ts="t",
            token_set=frozenset({"kelly", "sizing"}),
        )
        for i in range(3)
    ]
    cluster = cmod.Cluster(seed=episodes[0], members=episodes)
    draft = cmod.distill_cluster(cluster)
    assert draft.summary.startswith("Pattern:")
    assert "Recurring theme" not in draft.summary


def test_distill_cluster_falls_back_when_llm_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM returning None (Ollama unreachable / NO_PATTERN) → heuristic body."""
    from agent_memory_lite.cognition import consolidation as cmod  # noqa: PLC0415

    monkeypatch.setattr(
        "agent_memory_lite.cognition.consolidation_llm.llm_distill_cluster",
        lambda **_kw: None,
    )
    monkeypatch.setenv("MEMORY_CONSOLIDATION_LLM_ENABLED", "true")

    episodes = [
        EpisodeView(
            id=f"e{i}",
            gist=f"kelly sizing {i}",
            ts="t",
            token_set=frozenset({"kelly", "sizing"}),
        )
        for i in range(2)
    ]
    cluster = cmod.Cluster(seed=episodes[0], members=episodes)
    draft = cmod.distill_cluster(cluster)
    assert "Recurring theme" in draft.summary
    assert "kelly" in draft.summary
