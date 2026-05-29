"""Brief surface for the open-issue store (plan step 9.5)."""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.cognition.brief_compose import compose_brief
from agent_memory_lite.cognition.brief_sections_watch import _build_open_issues
from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations
from agent_memory_lite.storage.writer import write


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    db = tmp_path / "m.db"
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    apply_migrations(c, MIGRATION_DIR)
    yield c
    c.close()


def _add_issue(
    conn: sqlite3.Connection, *, title: str, severity: str, status: str = "open"
) -> None:
    write(
        conn,
        workspace_id="w",
        kind="issue",
        payload={"title": title, "category": "tech_debt", "severity": severity, "status": status},
    )


def test_no_issues_renders_empty_section(conn: sqlite3.Connection) -> None:
    section = _build_open_issues(conn, "w", budget=80)
    assert section.name == "open_issues"
    assert section.lines == []  # empty -> budget gets redistributed


def test_open_issue_is_surfaced(conn: sqlite3.Connection) -> None:
    _add_issue(conn, title="inline LLM blocks writes", severity="major")
    section = _build_open_issues(conn, "w", budget=80)
    body = "\n".join(section.lines)
    assert "## Open issues (debt/risk)" in body
    assert "[major]" in body
    assert "tech_debt:issue_" in body


def test_closed_issues_are_not_surfaced(conn: sqlite3.Connection) -> None:
    _add_issue(conn, title="already fixed", severity="critical", status="fixed")
    assert _build_open_issues(conn, "w", budget=80).lines == []


def test_severity_orders_critical_before_major(conn: sqlite3.Connection) -> None:
    _add_issue(conn, title="a major one", severity="major")
    _add_issue(conn, title="a critical one", severity="critical")
    lines = _build_open_issues(conn, "w", budget=200).lines
    issue_lines = [line for line in lines if line.startswith("- ")]
    assert issue_lines[0].startswith("- [critical]")
    assert issue_lines[1].startswith("- [major]")


def test_pre_migration_db_renders_empty(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE issues")
    conn.commit()
    assert _build_open_issues(conn, "w", budget=80).lines == []  # OperationalError -> empty


def test_compose_brief_includes_open_issues(conn: sqlite3.Connection) -> None:
    _add_issue(conn, title="hung memory_write on cold model load", severity="critical")
    brief = compose_brief(conn, workspace_id="w", max_tokens=500)
    assert "Open issues" in brief.body_md
    assert "[critical]" in brief.body_md
