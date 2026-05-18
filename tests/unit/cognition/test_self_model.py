"""Phase 5: self_model refresh + heuristic + brief integration."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.cognition.brief import compose_brief
from agent_memory_lite.cognition.self_model import (
    _TARGET_MAX_WORDS,
    _TARGET_MIN_WORDS,
    load_self_model,
    refresh_self_model,
)
from agent_memory_lite.utils.time import iso_now

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
OUTCOME_PATH = (
    Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0002_outcome_loop.sql"
)
SELF_MODEL_PATH = (
    Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0006_self_model.sql"
)


@pytest.fixture(autouse=True)
def _isolate_brief_cache() -> Iterator[None]:
    from agent_memory_lite.cognition import brief as brief_mod  # noqa: PLC0415

    brief_mod._BRIEF_CACHE.clear()
    yield
    brief_mod._BRIEF_CACHE.clear()


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.executescript(OUTCOME_PATH.read_text(encoding="utf-8"))
    c.executescript(SELF_MODEL_PATH.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _seed_decision(
    conn: sqlite3.Connection,
    *,
    id_: str,
    title: str = "T",
    gist: str = "decision body",
    outcome: float = 0.5,
    pinned: int = 0,
    status: str = "active",
) -> None:
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at, outcome_score, pinned)
           VALUES (?, 'ws', ?, 'body', ?, ?, ?, ?, ?, ?, ?)""",
        (id_, title, gist, status, iso_now(), iso_now(), iso_now(), outcome, pinned),
    )
    conn.commit()


def _seed_behavior(
    conn: sqlite3.Connection,
    *,
    id_: str,
    name: str = "beh",
    rule: str = "do the thing",
    outcome: float = 0.4,
) -> None:
    conn.execute(
        """INSERT INTO behaviors
           (id, workspace_id, name, kind, scope, priority, rule, rule_one_line,
            applies_to_json, active, pinned, outcome_score, created_at, updated_at)
           VALUES (?, 'ws', ?, 'operating_rule', 'workspace', 'project_convention',
                   ?, ?, '[]', 1, 1, ?, ?, ?)""",
        (id_, name, rule, rule[:120], outcome, iso_now(), iso_now()),
    )
    conn.commit()


def _seed_rejected_theory(
    conn: sqlite3.Connection, *, id_: str, claim: str = "false claim"
) -> None:
    conn.execute(
        """INSERT INTO theories
           (id, workspace_id, title, claim, gist, status, created_at, updated_at)
           VALUES (?, 'ws', 'T', ?, ?, 'rejected', ?, ?)""",
        (id_, claim, claim[:80], iso_now(), iso_now()),
    )
    conn.commit()


# ============================================================
# refresh writes one row, fills invariants + uncertainties
# ============================================================


def test_refresh_writes_one_row(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_x", outcome=0.6, gist="prefer quarter-Kelly sizing")
    model = refresh_self_model(conn, workspace_id="ws")
    assert model is not None
    assert model.workspace_id == "ws"
    assert "ws" in model.identity_text
    # Row persisted.
    row = conn.execute("SELECT identity_text FROM self_model WHERE workspace_id = 'ws'").fetchone()
    assert row is not None
    assert row[0] == model.identity_text


def test_invariants_contain_only_positive_outcome_decisions(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_pos", outcome=0.7, gist="good")
    _seed_decision(conn, id_="dec_neg", outcome=-0.5, gist="bad")
    model = refresh_self_model(conn, workspace_id="ws")
    assert model is not None
    assert "dec_pos" in model.invariants
    assert "dec_neg" not in model.invariants


def test_uncertainties_contain_rejected_theories(conn: sqlite3.Connection) -> None:
    _seed_rejected_theory(conn, id_="th_wrong", claim="raise hard SL floor")
    model = refresh_self_model(conn, workspace_id="ws")
    assert model is not None
    assert "th_wrong" in model.uncertainties


def test_identity_text_word_count_within_band(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_a", gist="approach a")
    _seed_decision(conn, id_="dec_b", gist="approach b")
    _seed_behavior(conn, id_="beh_1", rule="rule one")
    model = refresh_self_model(conn, workspace_id="ws")
    assert model is not None
    word_count = len(model.identity_text.split())
    assert _TARGET_MIN_WORDS <= word_count <= _TARGET_MAX_WORDS


def test_empty_workspace_still_writes_a_model(conn: sqlite3.Connection) -> None:
    """No decisions / behaviours / theories → narrative still in target band."""
    model = refresh_self_model(conn, workspace_id="ws_empty")
    assert model is not None
    assert _TARGET_MIN_WORDS <= len(model.identity_text.split()) <= _TARGET_MAX_WORDS


# ============================================================
# load_self_model round-trips invariants / uncertainties
# ============================================================


def test_load_self_model_round_trip(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_x", outcome=0.5)
    refresh_self_model(conn, workspace_id="ws")
    loaded = load_self_model(conn, workspace_id="ws")
    assert loaded is not None
    assert "dec_x" in loaded.invariants
    assert loaded.refreshed_via == "heuristic"


def test_load_returns_none_for_unknown_workspace(conn: sqlite3.Connection) -> None:
    assert load_self_model(conn, workspace_id="never_existed") is None


# ============================================================
# brief surfaces identity_text as first non-title line
# ============================================================


def test_brief_surfaces_identity_text(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id_="dec_x", outcome=0.5, gist="use quarter-Kelly")
    refresh_self_model(conn, workspace_id="ws")
    brief = compose_brief(conn, workspace_id="ws")
    lines = brief.body_md.splitlines()
    assert lines[0] == "# ws"
    # Identity text appears between the title and any "Workspace overview"
    # counts line.
    self_model_line = lines[1]
    assert "I work on ws" in self_model_line


def test_brief_without_self_model_falls_back(conn: sqlite3.Connection) -> None:
    """No self_model row → brief still works; identity section just lacks the line."""
    _seed_decision(conn, id_="dec_x", outcome=0.5)
    # Do NOT call refresh_self_model.
    brief = compose_brief(conn, workspace_id="ws")
    assert "# ws" in brief.body_md
    # No "I work on ws" line.
    assert "I work on ws" not in brief.body_md


def test_pre_migration_db_returns_none() -> None:
    """No self_model table → refresh returns None, brief still composes."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.executescript(OUTCOME_PATH.read_text(encoding="utf-8"))
    # Do NOT apply 0006.
    assert refresh_self_model(c, workspace_id="ws") is None
    assert load_self_model(c, workspace_id="ws") is None
    c.close()
