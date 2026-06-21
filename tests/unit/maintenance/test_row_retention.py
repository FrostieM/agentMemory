"""Safety tests for Phase-4 row retention.

The episode prune deletes data with FK enforcement OFF, so these tests pin
exactly what is KEPT vs DELETED. A regression that widened a delete would fail
here loudly.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.maintenance.row_retention import prune_retention

_NOW = datetime.now(UTC)
_NOW_ISO = _NOW.isoformat()


def _ago(days: int) -> str:
    return (_NOW - timedelta(days=days)).isoformat()


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


def _episode(c, *, id_, created_at, source_type="file_indexed", ws="ws") -> None:
    c.execute(
        "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
        "VALUES (?, ?, ?, 'x', ?)",
        (id_, ws, source_type, created_at),
    )


def _chunk(c, *, id_, episode_id, ws="ws") -> None:
    c.execute(
        "INSERT INTO chunks (id, workspace_id, kind, text, created_at, episode_id) "
        "VALUES (?, ?, 'code', 't', ?, ?)",
        (id_, ws, _NOW_ISO, episode_id),
    )


def _theory_evidence(c, *, id_, episode_id, ws="ws") -> None:
    c.execute(
        "INSERT INTO theory_evidence "
        "(id, workspace_id, theory_id, kind, summary, observed_at, created_at, source_episode_id) "
        "VALUES (?, ?, 'th_1', 'support', 's', ?, ?, ?)",
        (id_, ws, _NOW_ISO, _NOW_ISO, episode_id),
    )


def _audit(c, *, id_, created_at, ws="ws", source_episode_id=None) -> None:
    c.execute(
        "INSERT INTO audit_log "
        "(id, workspace_id, action, target_type, target_id, created_at, source_episode_id) "
        "VALUES (?, ?, 'ingest_file', 'file', 'f', ?, ?)",
        (id_, ws, created_at, source_episode_id),
    )


def _feedback(c, *, id_, created_at, ws="ws") -> None:
    c.execute(
        "INSERT INTO memory_usage_feedback "
        "(id, workspace_id, source_type, source_id, created_at) "
        "VALUES (?, ?, 'implicit_consolidation', 's', ?)",
        (id_, ws, created_at),
    )


def _causal(c, *, id_, relation, created_at, ws="ws") -> None:
    c.execute(
        "INSERT INTO causal_links "
        "(id, workspace_id, src_kind, src_id, dst_kind, dst_id, relation, created_at) "
        "VALUES (?, ?, 'decision', ?, 'decision', ?, ?, ?)",
        (id_, ws, f"src_{id_}", f"dst_{id_}", relation, created_at),
    )


def _insight(c, *, id_, episode_ids, ws="ws") -> None:
    c.execute(
        "INSERT INTO insights "
        "(id, workspace_id, insight_type, summary, created_at, updated_at, source_episode_ids_json) "
        "VALUES (?, ?, 'consolidation', 's', ?, ?, ?)",
        (id_, ws, _NOW_ISO, _NOW_ISO, json.dumps(episode_ids)),
    )


def _run(c, **overrides) -> object:
    settings = Settings().model_copy(update=overrides) if overrides else Settings()
    return prune_retention(
        c,
        workspace_id="ws",
        settings=settings,
        now_iso=_NOW_ISO,
        cap=settings.retention_max_per_table_per_pass,
    )


def _exists(c, table, id_) -> bool:
    return c.execute(f"SELECT 1 FROM {table} WHERE id = ?", (id_,)).fetchone() is not None


# ----- audit_log / feedback: simple age prune, no incoming refs -----


def test_audit_log_pruned_by_age(conn: sqlite3.Connection) -> None:
    _audit(conn, id_="a_old", created_at=_ago(120))
    _audit(conn, id_="a_new", created_at=_ago(5))
    conn.commit()
    rep = _run(conn)
    assert rep.audit_rows_pruned == 1
    assert not _exists(conn, "audit_log", "a_old")
    assert _exists(conn, "audit_log", "a_new")


def test_usage_feedback_pruned_by_age(conn: sqlite3.Connection) -> None:
    _feedback(conn, id_="f_old", created_at=_ago(120))
    _feedback(conn, id_="f_new", created_at=_ago(5))
    conn.commit()
    rep = _run(conn)
    assert rep.usage_feedback_pruned == 1
    assert not _exists(conn, "memory_usage_feedback", "f_old")
    assert _exists(conn, "memory_usage_feedback", "f_new")


# ----- causal_links: only re-derived relations, asserted markers kept -----


def test_causal_rederived_pruned_asserted_kept(conn: sqlite3.Connection) -> None:
    _causal(conn, id_="c_derived", relation="derived_from", created_at=_ago(120))
    _causal(conn, id_="c_similar", relation="semantically_similar_to", created_at=_ago(120))
    _causal(conn, id_="c_caused", relation="caused", created_at=_ago(120))
    _causal(conn, id_="c_invalid", relation="invalidated", created_at=_ago(120))
    _causal(conn, id_="c_recent", relation="derived_from", created_at=_ago(5))
    conn.commit()
    rep = _run(conn)
    assert rep.causal_links_pruned == 2
    assert not _exists(conn, "causal_links", "c_derived")
    assert not _exists(conn, "causal_links", "c_similar")
    assert _exists(conn, "causal_links", "c_caused")  # asserted -> kept
    assert _exists(conn, "causal_links", "c_invalid")  # invalidation marker -> kept
    assert _exists(conn, "causal_links", "c_recent")  # inside window -> kept


# ----- episodes: the delicate one. KEEP rules. -----


def test_file_episode_with_chunk_kept(conn: sqlite3.Connection) -> None:
    _episode(conn, id_="ep_chunked", created_at=_ago(120))
    _chunk(conn, id_="ch_1", episode_id="ep_chunked")
    conn.commit()
    rep = _run(conn)
    assert rep.file_episodes_pruned == 0
    assert _exists(conn, "episodes", "ep_chunked")


def test_file_episode_with_evidence_ref_kept(conn: sqlite3.Connection) -> None:
    _episode(conn, id_="ep_cited", created_at=_ago(120))
    _theory_evidence(conn, id_="te_1", episode_id="ep_cited")
    conn.commit()
    rep = _run(conn)
    assert rep.file_episodes_pruned == 0
    assert _exists(conn, "episodes", "ep_cited")  # cited provenance -> kept


def test_file_episode_cited_by_insight_json_kept(conn: sqlite3.Connection) -> None:
    # Regression: insights.source_episode_ids_json holds episode ids as a JSON
    # ARRAY, invisible to the scalar FK guard. An aged file_indexed episode cited
    # there must be KEPT -- otherwise the prune orphans the insight's provenance.
    _episode(conn, id_="ep_json_cited", created_at=_ago(120))
    _insight(conn, id_="ins_1", episode_ids=["other_ep", "ep_json_cited"])
    conn.commit()
    rep = _run(conn)
    assert rep.file_episodes_pruned == 0
    assert _exists(conn, "episodes", "ep_json_cited")


def test_file_episode_with_live_audit_ref_kept(conn: sqlite3.Connection) -> None:
    # An episode referenced by a NOT-YET-aged audit row is kept (no dangling ref);
    # it only becomes prunable once that audit row ages out.
    _episode(conn, id_="ep_auditref", created_at=_ago(120))
    _audit(conn, id_="a_ref", created_at=_ago(5), source_episode_id="ep_auditref")
    conn.commit()
    rep = _run(conn)
    assert rep.file_episodes_pruned == 0
    assert _exists(conn, "episodes", "ep_auditref")


def test_recent_file_episode_kept(conn: sqlite3.Connection) -> None:
    _episode(conn, id_="ep_recent", created_at=_ago(5))  # inside 30d window
    conn.commit()
    rep = _run(conn)
    assert rep.file_episodes_pruned == 0
    assert _exists(conn, "episodes", "ep_recent")


def test_agent_action_episode_never_pruned(conn: sqlite3.Connection) -> None:
    # The substantive memory: never touched, regardless of age or references.
    _episode(conn, id_="ep_agent", created_at=_ago(365), source_type="agent_action")
    conn.commit()
    rep = _run(conn)
    assert rep.file_episodes_pruned == 0
    assert _exists(conn, "episodes", "ep_agent")


# ----- episodes: DELETE rule + cascade -----


def test_orphan_file_episode_pruned(conn: sqlite3.Connection) -> None:
    _episode(conn, id_="ep_orphan", created_at=_ago(120))  # no chunk, no ref
    conn.commit()
    rep = _run(conn)
    assert rep.file_episodes_pruned == 1
    assert not _exists(conn, "episodes", "ep_orphan")


def test_audit_then_episode_cascade_same_pass(conn: sqlite3.Connection) -> None:
    # Episode referenced ONLY by an aged-out audit row: audit_log is pruned
    # first, releasing the episode to be pruned in the same pass.
    _episode(conn, id_="ep_casc", created_at=_ago(120))
    _audit(conn, id_="a_casc", created_at=_ago(120), source_episode_id="ep_casc")
    conn.commit()
    rep = _run(conn)
    assert rep.audit_rows_pruned == 1
    assert rep.file_episodes_pruned == 1
    assert not _exists(conn, "audit_log", "a_casc")
    assert not _exists(conn, "episodes", "ep_casc")


# ----- isolation + bounds + soft-fail -----


def test_workspace_isolation(conn: sqlite3.Connection) -> None:
    _audit(conn, id_="a_other", created_at=_ago(120), ws="ws-other")
    _episode(conn, id_="ep_other", created_at=_ago(120), ws="ws-other")
    conn.commit()
    rep = _run(conn)
    assert rep.audit_rows_pruned == 0
    assert rep.file_episodes_pruned == 0
    assert _exists(conn, "audit_log", "a_other")
    assert _exists(conn, "episodes", "ep_other")


def test_per_pass_cap_bounds_deletions(conn: sqlite3.Connection) -> None:
    for i in range(5):
        _audit(conn, id_=f"a_{i}", created_at=_ago(120))
    conn.commit()
    rep = _run(conn, retention_max_per_table_per_pass=2)
    assert rep.audit_rows_pruned == 2  # capped, the rest remain for the next pass
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 3


def test_one_table_failure_does_not_abort_others(conn: sqlite3.Connection) -> None:
    _audit(conn, id_="a_old", created_at=_ago(120))
    conn.execute("DROP TABLE causal_links")  # force the causal prune to raise
    conn.commit()
    rep = _run(conn)
    assert rep.audit_rows_pruned == 1  # audit still pruned despite causal failing
    assert any("causal_links" in e for e in rep.errors)
