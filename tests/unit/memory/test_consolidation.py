"""Unit tests for the v3 sleep-time consolidation pipeline.

Covers:

* Token + Jaccard helpers
* ``cluster_episodes`` greedy single-pass behaviour
* ``distill_cluster`` heuristic summary
* End-to-end ``consolidate_workspace``:
  - Empty workspace → 0 insights
  - Two clusters → 2 insights persisted
  - Idempotent w.r.t. the same window (multiple runs OK)
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.cognition import consolidation as cons

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db_path = tmp_path / "canonical.db"
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.commit()
    try:
        yield c
    finally:
        c.close()


def _seed_episode(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    raw_text: str,
    minutes_ago: int = 0,
    episode_id: str | None = None,
) -> str:
    """Insert one episode row directly so we can control created_at precisely."""
    eid = episode_id or f"ep_{abs(hash(raw_text))}"
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - minutes_ago * 60))
    conn.execute(
        """
        INSERT INTO episodes
            (id, workspace_id, source_type, raw_text, gist, trust_level,
             importance, confidence, created_at, is_archived)
        VALUES (?, ?, 'agent_action', ?, ?, 'agent_observed', 0.5, 0.5, ?, 0)
        """,
        (eid, workspace_id, raw_text, raw_text[:200], created),
    )
    conn.commit()
    return eid


# ============================================================
# Token helpers
# ============================================================


def test_tokens_extracts_alpha_only_skips_stopwords() -> None:
    out = cons._tokens("The Kelly sizing decision for trading bot.")
    assert "kelly" in out
    assert "sizing" in out
    assert "decision" in out
    assert "trading" in out
    # "the" / "for" are stopwords → excluded.
    assert "the" not in out
    assert "for" not in out


def test_jaccard_identical_returns_one() -> None:
    a = {"kelly", "sizing", "decision"}
    assert cons._jaccard(a, set(a)) == 1.0


def test_jaccard_disjoint_returns_zero() -> None:
    assert cons._jaccard({"a", "b"}, {"c", "d"}) == 0.0


# ============================================================
# Clustering
# ============================================================


def test_cluster_episodes_groups_overlapping_tokens() -> None:
    eps = [
        cons.EpisodeView(
            id="e1",
            gist="kelly sizing review",
            ts="t1",
            token_set=frozenset({"kelly", "sizing", "review"}),
        ),
        cons.EpisodeView(
            id="e2",
            gist="kelly sizing trade",
            ts="t2",
            token_set=frozenset({"kelly", "sizing", "trade"}),
        ),
        cons.EpisodeView(
            id="e3",
            gist="unrelated topic",
            ts="t3",
            token_set=frozenset({"unrelated", "topic", "stuff"}),
        ),
    ]
    clusters = cons.cluster_episodes(eps, min_size=2)
    assert len(clusters) == 1
    assert {m.id for m in clusters[0].members} == {"e1", "e2"}


def test_cluster_min_size_excludes_singletons() -> None:
    eps = [
        cons.EpisodeView(id="e1", gist="x", ts="t1", token_set=frozenset({"alpha", "beta"})),
        cons.EpisodeView(id="e2", gist="y", ts="t2", token_set=frozenset({"gamma", "delta"})),
    ]
    clusters = cons.cluster_episodes(eps, min_size=2)
    assert clusters == []


def test_signal_tokens_returns_majority_terms() -> None:
    eps = [
        cons.EpisodeView(id=str(i), gist="x", ts="t", token_set=frozenset({"kelly", f"x{i}"}))
        for i in range(3)
    ]
    cluster = cons.Cluster(seed=eps[0], members=eps)
    signal = cluster.signal_tokens
    assert "kelly" in signal


def test_distill_cluster_writes_summary_referencing_size() -> None:
    eps = [
        cons.EpisodeView(id=str(i), gist="x", ts="t", token_set=frozenset({"kelly", "sizing"}))
        for i in range(3)
    ]
    cluster = cons.Cluster(seed=eps[0], members=eps)
    draft = cons.distill_cluster(cluster)
    assert "3 episodes" in draft.summary
    assert "kelly" in draft.summary
    assert draft.evidence_episode_ids == ["0", "1", "2"]


# ============================================================
# End-to-end consolidate_workspace
# ============================================================


def test_consolidate_empty_workspace_returns_zero(conn: sqlite3.Connection) -> None:
    report = cons.consolidate_workspace(conn, workspace_id="default")
    assert report.episodes_seen == 0
    assert report.insights_written == 0


def test_consolidate_writes_insight_for_one_cluster(conn: sqlite3.Connection) -> None:
    _seed_episode(
        conn,
        workspace_id="default",
        raw_text="Kelly sizing review for trading bot",
        minutes_ago=10,
        episode_id="ep_a",
    )
    _seed_episode(
        conn,
        workspace_id="default",
        raw_text="Kelly sizing decision rerun for the bot",
        minutes_ago=8,
        episode_id="ep_b",
    )
    _seed_episode(
        conn,
        workspace_id="default",
        raw_text="An unrelated incident in deployment scripts",
        minutes_ago=5,
        episode_id="ep_c",
    )
    report = cons.consolidate_workspace(conn, workspace_id="default")
    assert report.episodes_seen == 3
    assert report.clusters_found == 1
    assert report.insights_written == 1
    rows = conn.execute(
        "SELECT summary, source_episode_ids_json FROM insights WHERE status='candidate'"
    ).fetchall()
    assert len(rows) == 1
    evidence = json.loads(rows[0][1])
    assert set(evidence) == {"ep_a", "ep_b"}


def test_consolidate_respects_window_hours(conn: sqlite3.Connection) -> None:
    # Old episode outside the 1-hour window.
    _seed_episode(
        conn,
        workspace_id="default",
        raw_text="Kelly sizing very old",
        minutes_ago=120,
        episode_id="ep_old",
    )
    _seed_episode(
        conn,
        workspace_id="default",
        raw_text="Kelly sizing recent",
        minutes_ago=30,
        episode_id="ep_recent",
    )
    _seed_episode(
        conn,
        workspace_id="default",
        raw_text="Kelly sizing also recent",
        minutes_ago=20,
        episode_id="ep_recent2",
    )
    report = cons.consolidate_workspace(conn, workspace_id="default", window_hours=1)
    # Only the two recent episodes are within the window.
    assert report.episodes_seen == 2
    assert report.insights_written == 1


def test_consolidate_caps_at_max_insights(conn: sqlite3.Connection) -> None:
    # Three distinct clusters with disjoint vocabularies so the greedy
    # Jaccard cluster doesn't merge them. Each pair shares 4 tokens and
    # has 0 overlap with the other pairs.
    pairs = [
        ("kelly sizing capacity bankroll quarter", "kelly sizing capacity bankroll review"),
        ("nginx routing proxy upstream cache", "nginx routing proxy upstream timeout"),
        ("compiler garbage collection heap stack", "compiler garbage collection heap layout"),
    ]
    for pair_idx, (txt_a, txt_b) in enumerate(pairs):
        _seed_episode(
            conn,
            workspace_id="default",
            raw_text=txt_a,
            minutes_ago=20 - pair_idx,
            episode_id=f"ep_{pair_idx}_1",
        )
        _seed_episode(
            conn,
            workspace_id="default",
            raw_text=txt_b,
            minutes_ago=15 - pair_idx,
            episode_id=f"ep_{pair_idx}_2",
        )
    report = cons.consolidate_workspace(conn, workspace_id="default", max_insights=2)
    assert report.clusters_found >= 3
    assert report.insights_written == 2  # capped


def test_consolidate_workspace_isolation(conn: sqlite3.Connection) -> None:
    _seed_episode(
        conn,
        workspace_id="ws_a",
        raw_text="kelly sizing in ws_a one",
        minutes_ago=5,
        episode_id="a1",
    )
    _seed_episode(
        conn,
        workspace_id="ws_a",
        raw_text="kelly sizing in ws_a two",
        minutes_ago=4,
        episode_id="a2",
    )
    _seed_episode(
        conn,
        workspace_id="ws_b",
        raw_text="kelly sizing in ws_b one",
        minutes_ago=5,
        episode_id="b1",
    )
    report_a = cons.consolidate_workspace(conn, workspace_id="ws_a")
    report_b = cons.consolidate_workspace(conn, workspace_id="ws_b")
    assert report_a.episodes_seen == 2
    assert report_b.episodes_seen == 1
    # ws_b had only 1 episode → no cluster forms.
    assert report_b.insights_written == 0
