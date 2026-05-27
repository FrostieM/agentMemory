"""Phase 4: lint elevates verdict to ``block`` on reflex violation."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.cognition.lint import lint
from agent_memory_lite.config.settings import get_settings, reset_settings_cache
from agent_memory_lite.utils.time import iso_now


@pytest.fixture(autouse=True)
def _reset_settings() -> Iterator[None]:
    reset_settings_cache()
    yield
    reset_settings_cache()


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


def _seed_rule(
    conn: sqlite3.Connection,
    *,
    rule_name: str,
    trigger_tool: str,
    enforcement: str = "block",
    precondition_kind: str = "impact_check_within_seconds",
) -> None:
    conn.execute(
        """INSERT INTO reflex_rules
           (id, workspace_id, rule_name, trigger_tool, trigger_pattern,
            precondition_kind, precondition_param_json, enforcement,
            active, created_at, updated_at)
           VALUES (?, 'ws', ?, ?, '', ?, '{}', ?, 1, ?, ?)""",
        (
            f"reflex_{rule_name}",
            rule_name,
            trigger_tool,
            precondition_kind,
            enforcement,
            iso_now(),
            iso_now(),
        ),
    )
    conn.commit()


def test_block_reflex_yields_block_verdict(conn: sqlite3.Connection) -> None:
    _seed_rule(conn, rule_name="strict-edit", trigger_tool="Edit", enforcement="block")
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "src/x.py"},
        transcript_path=None,
    )
    assert result.verdict == "block"
    assert len(result.reflex_violations) == 1
    assert "[reflex]" in result.diagnostic


def test_advisory_reflex_yields_allow_with_advisories(conn: sqlite3.Connection) -> None:
    _seed_rule(conn, rule_name="soft-edit", trigger_tool="Edit", enforcement="advisory")
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "src/x.py"},
        transcript_path=None,
    )
    assert result.verdict == "allow_with_advisories"
    assert len(result.reflex_violations) == 1
    assert result.reflex_violations[0]["enforcement"] == "advisory"


def test_no_reflex_no_change(conn: sqlite3.Connection) -> None:
    """Empty reflex_rules table → reflex_violations==[]; verdict 'allow' (clean)."""
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "src/x.py"},
        transcript_path=None,
    )
    assert result.reflex_violations == []
    # verdict could still be allow_with_advisories from discipline_hint;
    # the contract is that reflex_violations stays empty.


def test_disabled_flag_skips_reflex(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MEMORY_REFLEX_ENABLED=false → no reflex violations even with active rules."""
    _seed_rule(conn, rule_name="strict", trigger_tool="Edit", enforcement="block")
    monkeypatch.setenv("MEMORY_REFLEX_ENABLED", "false")
    reset_settings_cache()
    assert get_settings().reflex_enabled is False
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "src/x.py"},
        transcript_path=None,
    )
    assert result.reflex_violations == []
    assert result.verdict != "block"
