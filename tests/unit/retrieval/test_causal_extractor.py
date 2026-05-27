"""Phase 7: causal_extractor tests."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.retrieval.causal_extractor import (
    extract_workspace,
    list_outgoing,
)
from agent_memory_lite.utils.time import iso_now


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
    id_: str,
    supersedes: str | None = None,
    source_episode_id: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, status, valid_from,
            created_at, updated_at, supersedes_decision_id, source_episode_id, pinned)
           VALUES (?, 'ws', 'T', 'B', 'active', ?, ?, ?, ?, ?, 0)""",
        (id_, iso_now(), iso_now(), iso_now(), supersedes, source_episode_id),
    )
    conn.commit()


def _seed_decision_with_title(
    conn: sqlite3.Connection,
    *,
    id_: str,
    title: str,
    supersedes: str | None = None,
) -> None:
    """Like _seed_decision but lets the test specify the title text --
    used by the Jaccard similarity gate tests."""
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, status, valid_from,
            created_at, updated_at, supersedes_decision_id, pinned)
           VALUES (?, 'ws', ?, 'B', 'active', ?, ?, ?, ?, 0)""",
        (id_, title, iso_now(), iso_now(), iso_now(), supersedes),
    )
    conn.commit()


def _seed_insight(
    conn: sqlite3.Connection,
    *,
    id_: str,
    episode_ids: list[str],
) -> None:
    conn.execute(
        """INSERT INTO insights
           (id, workspace_id, insight_type, summary, gist, status, confidence,
            source_episode_ids_json, created_at, updated_at)
           VALUES (?, 'ws', 'consolidation', 's', 'g', 'candidate', 0.6, ?, ?, ?)""",
        (id_, json.dumps(episode_ids), iso_now(), iso_now()),
    )
    conn.commit()


# ============================================================
# invalidated links from supersedes
# ============================================================


def test_supersedes_yields_invalidated_link(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_old")
    conn.execute(
        """INSERT INTO episodes
           (id, workspace_id, source_type, raw_text, created_at)
           VALUES ('ep_x', 'ws', 'agent_action', 'evidence', ?)""",
        (iso_now(),),
    )
    conn.commit()
    _seed_decision(conn, id_="dec_new", supersedes="dec_old", source_episode_id="ep_x")
    report = extract_workspace(conn, workspace_id="ws")
    assert report.invalidated_links == 1
    links = list_outgoing(conn, workspace_id="ws", src_kind="decision", src_id="dec_new")
    assert len(links) == 1
    assert links[0]["relation"] == "invalidated"
    assert links[0]["dst_id"] == "dec_old"
    assert links[0]["evidence_episode_id"] == "ep_x"


def test_metadata_refresh_supersedes_does_not_yield_invalidated(
    conn: sqlite3.Connection,
) -> None:
    """A 'Provenance refresh: X' -> 'X' supersedes is metadata bookkeeping,
    NOT a semantic invalidation. The Jaccard similarity gate must skip it."""
    _seed_decision_with_title(conn, id_="dec_old", title="Hard reset 2026-05-04")
    _seed_decision_with_title(
        conn,
        id_="dec_new",
        title="Provenance refresh: Hard reset 2026-05-04",
        supersedes="dec_old",
    )
    report = extract_workspace(conn, workspace_id="ws")
    assert report.invalidated_links == 0
    links = list_outgoing(conn, workspace_id="ws", src_kind="decision", src_id="dec_new")
    assert links == []


def test_architectural_pivot_supersedes_yields_invalidated(
    conn: sqlite3.Connection,
) -> None:
    """A real semantic pivot (different vocabulary) MUST surface as
    invalidated, even if titles share some tokens."""
    _seed_decision_with_title(
        conn,
        id_="dec_old",
        title="PR1 architectural realignment deployed Layer 1+4+7",
    )
    _seed_decision_with_title(
        conn,
        id_="dec_new",
        title="Layer 1.5 deployed: queue trusts v2 (closes selector vs runtime drift)",
        supersedes="dec_old",
    )
    report = extract_workspace(conn, workspace_id="ws")
    assert report.invalidated_links == 1


def test_short_titles_still_yield_invalidated_when_truly_different(
    conn: sqlite3.Connection,
) -> None:
    """Short titles with ZERO real overlap should still produce invalidated."""
    _seed_decision_with_title(conn, id_="dec_old", title="Kelly bidirectional sizing")
    _seed_decision_with_title(
        conn,
        id_="dec_new",
        title="Conservative tier scaling replaces aggressive sizing",
        supersedes="dec_old",
    )
    report = extract_workspace(conn, workspace_id="ws")
    assert report.invalidated_links == 1


def test_idempotent_extraction(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_old")
    _seed_decision(conn, id_="dec_new", supersedes="dec_old")
    first = extract_workspace(conn, workspace_id="ws")
    second = extract_workspace(conn, workspace_id="ws")
    assert first.invalidated_links == 1
    assert second.invalidated_links == 0


def test_no_supersedes_no_invalidated_link(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_lone")
    report = extract_workspace(conn, workspace_id="ws")
    assert report.invalidated_links == 0


# ============================================================
# derived_from links from insight episode citations
# ============================================================


def test_insight_episodes_yield_derived_from(conn: sqlite3.Connection) -> None:
    _seed_insight(conn, id_="ins_x", episode_ids=["ep_1", "ep_2", "ep_3"])
    report = extract_workspace(conn, workspace_id="ws")
    assert report.derived_links == 3
    links = list_outgoing(conn, workspace_id="ws", src_kind="insight", src_id="ins_x")
    assert {link["dst_id"] for link in links} == {"ep_1", "ep_2", "ep_3"}
    assert all(link["relation"] == "derived_from" for link in links)


def test_insight_with_empty_episode_list(conn: sqlite3.Connection) -> None:
    _seed_insight(conn, id_="ins_empty", episode_ids=[])
    report = extract_workspace(conn, workspace_id="ws")
    assert report.derived_links == 0


def test_insight_with_bad_json_skips(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT INTO insights
           (id, workspace_id, insight_type, summary, status, confidence,
            source_episode_ids_json, created_at, updated_at)
           VALUES ('ins_bad', 'ws', 'consolidation', 's', 'candidate', 0.5,
                   'not valid json', ?, ?)""",
        (iso_now(), iso_now()),
    )
    conn.commit()
    report = extract_workspace(conn, workspace_id="ws")
    assert report.derived_links == 0


# ============================================================
# pre-migration safety
# ============================================================


def test_pre_migration_db_returns_zero_report() -> None:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(c)
    # No causal_links table.
    report = extract_workspace(c, workspace_id="ws")
    assert report.invalidated_links == 0
    assert report.derived_links == 0
    c.close()


def test_list_outgoing_filter_by_relation(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_old")
    _seed_decision(conn, id_="dec_new", supersedes="dec_old")
    extract_workspace(conn, workspace_id="ws")
    links_inv = list_outgoing(
        conn,
        workspace_id="ws",
        src_kind="decision",
        src_id="dec_new",
        relation="invalidated",
    )
    links_other = list_outgoing(
        conn,
        workspace_id="ws",
        src_kind="decision",
        src_id="dec_new",
        relation="derived_from",
    )
    assert len(links_inv) == 1
    assert links_other == []
