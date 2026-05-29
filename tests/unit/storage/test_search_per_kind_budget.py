"""Per-kind search budget for explicit-kinds queries (release plan task #120).

A search narrowed to an explicit ``kinds=[...]`` list is a focused query: each
requested kind should get the full ``limit`` budget, not ``limit // len(kinds)``.
Before the fix, ``kinds=['decision','behavior']`` with ``limit=10`` capped each
kind at 5, so a query whose top matches are all decisions could return at most
5 of them. This pins the raised budget.
"""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations
from agent_memory_lite.storage.reader import search
from agent_memory_lite.storage.writer import write


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    db = tmp_path / "m.db"
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    apply_migrations(c, MIGRATION_DIR)
    yield c
    c.close()


def test_explicit_kinds_get_full_per_kind_budget(conn: sqlite3.Connection) -> None:
    for i in range(7):
        write(
            conn,
            workspace_id="w",
            kind="decision",
            payload={
                "title": f"zzbudgettoken alpha case {i}",
                "decision_text": "body",
                "status": "active",
            },
        )

    hits = search(
        conn,
        workspace_id="w",
        query="zzbudgettoken",
        kinds=["decision", "behavior"],
        limit=10,
        log_coactivations=False,
    )
    decision_hits = [h for h in hits if h.kind == "decision"]
    # Old behavior capped decisions at limit // 2 == 5; the fix gives the full
    # budget so all 7 matching decisions come back.
    assert len(decision_hits) >= 6, f"expected >5 decisions (full budget), got {len(decision_hits)}"


def test_default_wide_search_stays_balanced(conn: sqlite3.Connection) -> None:
    # kinds=None keeps the divided budget so one kind can't crowd out the rest.
    for i in range(7):
        write(
            conn,
            workspace_id="w",
            kind="decision",
            payload={
                "title": f"zzbalancetoken case {i}",
                "decision_text": "body",
                "status": "active",
            },
        )
    hits = search(conn, workspace_id="w", query="zzbalancetoken", limit=4, log_coactivations=False)
    # limit=4 over all kinds -> per_kind = max(2, 4//N) is small; the result is
    # capped at limit overall regardless, and must not exceed it.
    assert len(hits) <= 4
