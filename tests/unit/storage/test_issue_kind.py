"""First-class issue/debt store kind (release plan step 9.2).

Covers the write -> compact projection -> get -> edit-lifecycle path for the
``issue`` kind: a durable record of bugs / tech debt / risks / errors with
severity, status, evidence, and a retest command. The kind rides the generic
storage path (``_KIND_META`` / ``_KIND_TABLES`` / ``_PROJECTORS``), so these
tests also guard that all three registries stay wired together.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations
from agent_memory_lite.ingestion.issue_writer import issue_signature, write_issue_canonical
from agent_memory_lite.storage.projections import known_kinds
from agent_memory_lite.storage.reader import VALID_KINDS, get_object, search_kind
from agent_memory_lite.storage.writer import edit, write


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    db = tmp_path / "m.db"
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    apply_migrations(c, MIGRATION_DIR)
    yield c
    c.close()


def _payload(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "inline LLM blocks the synchronous write path",
        "category": "tech_debt",
        "severity": "major",
        "status": "open",
        "source": "agent",
    }
    base.update(over)
    return base


def test_migration_creates_issues_table(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(issues)").fetchall()}
    assert {"id", "workspace_id", "title", "category", "severity", "status"} <= cols


def test_issue_kind_is_registered_everywhere() -> None:
    # The kind must be wired in all three registries or write/get silently break.
    assert "issue" in known_kinds()
    assert "issue" in VALID_KINDS


def test_write_issue_returns_compact_projection(conn: sqlite3.Connection) -> None:
    proj = write(conn, workspace_id="w", kind="issue", payload=_payload())
    assert proj is not None
    assert proj["kind"] == "issue"
    assert proj["category"] == "tech_debt"
    assert proj["severity"] == "major"
    assert proj["status"] == "open"
    assert proj["gist"]  # gist auto-derived from title


def test_issue_row_persists_evidence_and_retest(conn: sqlite3.Connection) -> None:
    proj = write(
        conn,
        workspace_id="w",
        kind="issue",
        payload=_payload(
            evidence_json=json.dumps(["src/agent_memory_lite/ingestion/auto_promote.py"]),
            retest_command="pytest -q tests/unit/ingestion/test_auto_promote_inline_timeout.py",
            description="inline Ollama generation runs before the write returns",
        ),
    )
    assert proj is not None
    row = conn.execute("SELECT * FROM issues WHERE id = ?", (proj["id"],)).fetchone()
    assert json.loads(row["evidence_json"]) == ["src/agent_memory_lite/ingestion/auto_promote.py"]
    assert row["retest_command"].startswith("pytest")
    assert row["source"] == "agent"


def test_get_issue_roundtrips(conn: sqlite3.Connection) -> None:
    proj = write(conn, workspace_id="w", kind="issue", payload=_payload())
    got = get_object(conn, workspace_id="w", kind="issue", object_id=proj["id"])
    assert got is not None
    assert got["kind"] == "issue"
    assert got["severity"] == "major"


def test_edit_status_open_to_fixed(conn: sqlite3.Connection) -> None:
    proj = write(conn, workspace_id="w", kind="issue", payload=_payload())
    ed = edit(
        conn,
        workspace_id="w",
        kind="issue",
        object_id=proj["id"],
        fields={"status": "fixed", "resolved_at": "2026-05-28T22:00:00Z"},
    )
    assert ed is not None
    assert ed["status"] == "fixed"


def test_workspace_isolation_on_get(conn: sqlite3.Connection) -> None:
    proj = write(conn, workspace_id="w", kind="issue", payload=_payload())
    # A foreign workspace must never resolve another workspace's issue.
    assert get_object(conn, workspace_id="other", kind="issue", object_id=proj["id"]) is None


# ---- signature dedup (plan step 9.3) -------------------------------------


def _count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0])


def test_signature_normalizes_title_and_category() -> None:
    assert issue_signature("  Hung   memory_write  ", "Tech_Debt") == "tech_debt|hung memory_write"


def test_open_duplicate_is_deduped(conn: sqlite3.Connection) -> None:
    first = write_issue_canonical(conn, workspace_id="w", payload=_payload())
    second = write_issue_canonical(conn, workspace_id="w", payload=_payload())
    assert first is not None
    assert second is not None
    assert first["id"] == second["id"]  # same open defect -> same row
    assert _count(conn) == 1


def test_recurrence_after_close_creates_new_row(conn: sqlite3.Connection) -> None:
    first = write_issue_canonical(conn, workspace_id="w", payload=_payload())
    edit(conn, workspace_id="w", kind="issue", object_id=first["id"], fields={"status": "fixed"})
    again = write_issue_canonical(conn, workspace_id="w", payload=_payload())
    assert again is not None
    assert again["id"] != first["id"]  # a closed issue does not block a recurrence
    assert _count(conn) == 2


def test_different_category_not_deduped(conn: sqlite3.Connection) -> None:
    write_issue_canonical(conn, workspace_id="w", payload=_payload(category="tech_debt"))
    write_issue_canonical(conn, workspace_id="w", payload=_payload(category="bug"))
    assert _count(conn) == 2


def test_signature_persisted_on_write(conn: sqlite3.Connection) -> None:
    proj = write_issue_canonical(conn, workspace_id="w", payload=_payload())
    sig = conn.execute("SELECT signature FROM issues WHERE id = ?", (proj["id"],)).fetchone()[0]
    assert sig == issue_signature("inline LLM blocks the synchronous write path", "tech_debt")


def test_issue_is_searchable(conn: sqlite3.Connection) -> None:
    """Regression: issue must be in _KIND_FTS_COLUMNS so memory_search finds it
    (token-overlap search returned [] before issue was registered there)."""
    proj = write(
        conn,
        workspace_id="w",
        kind="issue",
        payload=_payload(title="zzdistinct flux capacitor leak in reranker"),
    )
    hits = search_kind(conn, workspace_id="w", kind="issue", query="flux capacitor")
    assert len(hits) >= 1
    assert any(h.projection.get("id") == proj["id"] for h in hits)
