"""Phase 6: bi-temporal validity filter."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_memory_lite.storage.bi_temporal import has_validity_columns, where_valid
from agent_memory_lite.storage.reader import list_kind
from agent_memory_lite.utils.time import iso_now

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
BI_TEMPORAL_PATH = (
    Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0007_bi_temporal.sql"
)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.executescript(BI_TEMPORAL_PATH.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ============================================================
# where_valid SQL fragment
# ============================================================


def test_where_valid_returns_fragment_and_params() -> None:
    clause, params = where_valid(as_of="2026-05-18T00:00:00+00:00")
    assert "valid_from" in clause
    assert "valid_to" in clause
    assert params == ("2026-05-18T00:00:00+00:00", "2026-05-18T00:00:00+00:00")


def test_where_valid_defaults_to_now() -> None:
    _clause, params = where_valid()
    assert params[0] == params[1]
    # ISO timestamp shape, length-ish.
    assert len(params[0]) >= 19


# ============================================================
# has_validity_columns probe
# ============================================================


def test_has_validity_columns_true_after_migration(conn: sqlite3.Connection) -> None:
    assert has_validity_columns(conn, "theories")
    assert has_validity_columns(conn, "concepts")
    assert has_validity_columns(conn, "behaviors")
    assert has_validity_columns(conn, "insights")


def test_has_validity_columns_false_pre_migration() -> None:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    # Do NOT apply 0007.
    assert not has_validity_columns(c, "theories")
    c.close()


def test_has_validity_columns_false_for_missing_table(conn: sqlite3.Connection) -> None:
    assert not has_validity_columns(conn, "this_table_does_not_exist")


# ============================================================
# list_kind respects as_of bracket
# ============================================================


def _seed_concept(
    conn: sqlite3.Connection,
    *,
    id_: str,
    name: str,
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO concepts
           (id, workspace_id, name, kind, definition, definition_one_line,
            aliases_json, created_at, updated_at, valid_from, valid_to)
           VALUES (?, 'ws', ?, 'term', ?, ?, '[]', ?, ?, ?, ?)""",
        (id_, name, f"def {id_}", f"def {id_}", iso_now(), iso_now(), valid_from, valid_to),
    )
    conn.commit()


def test_concept_with_open_validity_appears_today(conn: sqlite3.Connection) -> None:
    _seed_concept(conn, id_="con_open", name="open")  # both NULL = forever-valid
    out = list_kind(conn, workspace_id="ws", kind="concept")
    assert any(c["id"] == "con_open" for c in out)


def test_concept_expired_in_past_excluded_today(conn: sqlite3.Connection) -> None:
    past = datetime.now(UTC) - timedelta(days=30)
    _seed_concept(conn, id_="con_expired", name="x", valid_to=_iso(past))
    out = list_kind(conn, workspace_id="ws", kind="concept")
    assert not any(c["id"] == "con_expired" for c in out)


def test_concept_not_yet_valid_excluded_today(conn: sqlite3.Connection) -> None:
    future = datetime.now(UTC) + timedelta(days=30)
    _seed_concept(conn, id_="con_future", name="x", valid_from=_iso(future))
    out = list_kind(conn, workspace_id="ws", kind="concept")
    assert not any(c["id"] == "con_future" for c in out)


def test_as_of_param_returns_historical_view(conn: sqlite3.Connection) -> None:
    """An expired concept reappears when as_of points before its expiry."""
    past_expiry = datetime.now(UTC) - timedelta(days=30)
    past_validity = datetime.now(UTC) - timedelta(days=90)
    _seed_concept(
        conn,
        id_="con_history",
        name="history",
        valid_from=_iso(past_validity),
        valid_to=_iso(past_expiry),
    )
    # Today: NOT visible (expired).
    today = list_kind(conn, workspace_id="ws", kind="concept")
    assert not any(c["id"] == "con_history" for c in today)
    # 60 days ago: visible.
    snapshot = datetime.now(UTC) - timedelta(days=60)
    historical = list_kind(conn, workspace_id="ws", kind="concept", as_of=_iso(snapshot))
    assert any(c["id"] == "con_history" for c in historical)


def test_pre_migration_db_still_returns_rows() -> None:
    """No valid_from/valid_to columns -> as_of param is a no-op, rows still come back."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    # Do NOT apply 0007.
    c.execute(
        """INSERT INTO concepts
           (id, workspace_id, name, kind, definition, definition_one_line,
            aliases_json, created_at, updated_at)
           VALUES ('con_legacy', 'ws', 'legacy', 'term', 'd', 'd1', '[]', ?, ?)""",
        (iso_now(), iso_now()),
    )
    c.commit()
    out = list_kind(c, workspace_id="ws", kind="concept", as_of=iso_now())
    assert any(c["id"] == "con_legacy" for c in out)
    c.close()
