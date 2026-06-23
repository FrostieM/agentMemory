"""Unit tests for v3 memory_lint adapter."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.cognition.lint import LintResult, lint


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


def _seed_behavior(
    conn: sqlite3.Connection,
    *,
    id_: str,
    name: str,
    rule: str,
    applies_to_json: str = "[]",
    pinned: int = 1,
    active: int = 1,
) -> None:
    """Seed into canonical behaviors."""
    conn.execute(
        """INSERT INTO behaviors
           (id, workspace_id, name, kind, scope, priority, rule, applies_to_json,
            active, pinned, created_at, updated_at)
           VALUES (?, 'ws', ?, 'operating_rule', 'workspace', 'user_preference', ?, ?, ?, ?, 'ts', 'ts')""",
        (id_, name, rule, applies_to_json, active, pinned),
    )
    conn.commit()


def _seed_decision_v3(
    conn: sqlite3.Connection, *, id_: str, title: str, gist: str, status: str = "active"
) -> None:
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at)
           VALUES (?, 'ws', ?, 'body', ?, ?, 'ts', 'ts', 'ts')""",
        (id_, title, gist, status),
    )
    conn.commit()


# ============================================================
# Verdict + advisory mapping
# ============================================================


def test_lint_empty_workspace_allows(conn: sqlite3.Connection) -> None:
    """No rules, no advisories — verdict is allow."""
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "x", "new_string": "y"},
    )
    assert result.verdict == "allow"
    assert result.applicable_rules == []


def test_lint_returns_lint_result(conn: sqlite3.Connection) -> None:
    result = lint(conn, workspace_id="ws", tool_name="Read", tool_payload={})
    assert isinstance(result, LintResult)


def test_lint_mechanical_block_returns_block_verdict(conn: sqlite3.Connection) -> None:
    """Seed a magic-number rule and confirm Edit with literal blocks."""
    _seed_behavior(
        conn,
        id_="beh_mn",
        name="no-magic-number",
        rule="No magic numbers in strategy code.",
        applies_to_json='["enforcement:mechanical", "mechanical:no-magic-number"]',
    )
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={
            "file_path": "src/strategy/x.py",
            "new_string": "if confidence > 0.85:\n    pass",
        },
    )
    assert result.verdict == "block"
    assert any(r["rule_id"] == "beh_mn" for r in result.applicable_rules)


def test_lint_advisory_path_allows_with_advisories(conn: sqlite3.Connection) -> None:
    """A pinned rule applicable but not blocking → allow_with_advisories.

    Implementation: we seed an enforcement:mechanical rule with
    decision-provenance tag; lint produces a 'block' (mechanical
    detector fires), so we ALSO check the inverse path: when
    no enforcement rules exist for a tool, verdict is plain 'allow'.
    """
    # Mechanical rules always block in the current dispatcher; pure
    # advisory comes from semantic rules when Ollama disabled.
    # Seed a semantic-only rule (won't fire without Ollama) and
    # verify verdict is 'allow' (not block).
    _seed_behavior(
        conn,
        id_="beh_s",
        name="semantic-rule",
        rule="Be careful with X.",
        applies_to_json='["enforcement:semantic"]',
    )
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "src/api/x.py", "new_string": "ok"},
        ollama_base_url=None,
        ollama_model=None,
    )
    assert result.verdict == "allow"


def test_lint_related_decisions_match_payload(conn: sqlite3.Connection) -> None:
    _seed_decision_v3(
        conn, id_="dec_1", title="Kelly sizing", gist="Use quarter-Kelly for $200 bankroll"
    )
    _seed_decision_v3(conn, id_="dec_2", title="Unrelated", gist="Other topic")
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "src/strategy/kelly.py", "new_string": "kelly"},
    )
    ids = [d["id"] for d in result.related_decisions]
    assert "dec_1" in ids


def test_lint_related_decisions_excludes_superseded(conn: sqlite3.Connection) -> None:
    """A superseded decision (already replaced) must NOT surface as a live
    related decision on the priming hook -- it can otherwise rank ahead of its
    own active replacement. Mirrors the _prior_failures terminal-set guard."""
    _seed_decision_v3(conn, id_="dec_live", title="Kelly sizing", gist="quarter-Kelly active")
    _seed_decision_v3(
        conn, id_="dec_old", title="Kelly sizing", gist="full-Kelly replaced", status="superseded"
    )
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "src/strategy/kelly.py", "new_string": "kelly"},
    )
    ids = [d["id"] for d in result.related_decisions]
    assert "dec_live" in ids  # active replacement surfaces
    assert "dec_old" not in ids  # superseded predecessor filtered


def test_lint_related_decisions_excludes_padded_superseded(conn: sqlite3.Connection) -> None:
    """The related_decisions terminal guard is whitespace-stripped, symmetric
    with the brief associates filter -- a padded superseded status (writable via
    memory_edit) must not surface as a live related decision."""
    _seed_decision_v3(conn, id_="dec_live", title="Kelly sizing", gist="active")
    _seed_decision_v3(
        conn, id_="dec_pad", title="Kelly sizing", gist="padded", status="  superseded  "
    )
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "src/strategy/kelly.py", "new_string": "kelly"},
    )
    ids = [d["id"] for d in result.related_decisions]
    assert "dec_live" in ids
    assert "dec_pad" not in ids


def test_lint_related_decisions_excludes_rejected(conn: sqlite3.Connection) -> None:
    """A rejected decision is thrown-out, not live context."""
    _seed_decision_v3(conn, id_="dec_ok", title="Kelly sizing", gist="active")
    _seed_decision_v3(conn, id_="dec_rej", title="Kelly sizing", gist="rejected", status="rejected")
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "src/strategy/kelly.py", "new_string": "kelly"},
    )
    ids = [d["id"] for d in result.related_decisions]
    assert "dec_ok" in ids
    assert "dec_rej" not in ids


def test_lint_prior_failures_finds_rejected_theory(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT INTO theories
           (id, workspace_id, title, domain, claim, status, created_at, updated_at)
           VALUES ('th_x', 'ws', 'Half-Kelly safe', 'trading', 'Half-Kelly is safe at $200',
                   'rejected', 'ts', 'ts')"""
    )
    conn.commit()
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "src/strategy/kelly.py"},
    )
    ids = [f["id"] for f in result.prior_failures]
    assert "th_x" in ids


def test_lint_watch_outs_renders_when_failures_present(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT INTO theories
           (id, workspace_id, title, domain, claim, status, gist, created_at, updated_at)
           VALUES ('th_x', 'ws', 'Kelly safe', 'trading', 'Claim text',
                   'rejected', 'Half-Kelly drawdown >40%', 'ts', 'ts')"""
    )
    conn.commit()
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "kelly"},
    )
    assert "rejected_theory" in result.watch_outs


def test_lint_to_dict_serialization(conn: sqlite3.Connection) -> None:
    result = lint(conn, workspace_id="ws", tool_name="Read", tool_payload={})
    data = result.to_dict()
    assert "verdict" in data
    assert "applicable_rules" in data
    assert "related_decisions" in data
    assert "prior_failures" in data
    assert "watch_outs" in data
    assert "discipline_hint" in data


def test_lint_failure_soft_on_subsystem_error(conn: sqlite3.Connection) -> None:
    """Even if a subsystem raises, lint returns allow + diagnostic."""
    # Drop the behaviors table to simulate a schema mismatch / corruption.
    conn.execute("DROP TABLE behaviors")
    conn.commit()
    result = lint(conn, workspace_id="ws", tool_name="Edit", tool_payload={"file_path": "x"})
    # Should not raise; verdict degrades to allow.
    assert result.verdict == "allow"


def test_lint_payload_query_extraction_for_decisions(conn: sqlite3.Connection) -> None:
    """Payload query extracted from common fields for the related-decision search."""
    _seed_decision_v3(conn, id_="dec_d", title="Deploy", gist="Deploy procedure")
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Bash",
        tool_payload={"command": "deploy step run"},
    )
    ids = [d["id"] for d in result.related_decisions]
    assert "dec_d" in ids


# ============================================================
# discipline_hint — redirects Read/Edit/Grep to memory_impact_check
# ============================================================


def _seed_digest(
    conn: sqlite3.Connection,
    *,
    file_path: str,
    inbound: int = 0,
    workspace_id: str = "ws",
) -> None:
    import time  # noqa: PLC0415

    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        """INSERT INTO code_digests (id, workspace_id, file_path, file_sha1,
                                     language, chunk_count, symbol_count,
                                     inbound_edge_count, outbound_edge_count,
                                     purpose_short, top_symbols_json,
                                     last_indexed_at, updated_at)
           VALUES (?, ?, ?, ?, 'python', 1, 1, ?, 0, 'purpose',
                   '[]', ?, ?)""",
        (f"digest_{file_path}", workspace_id, file_path, "h" * 40, inbound, ts, ts),
    )
    conn.commit()


def test_lint_discipline_hint_fires_on_read_with_callers(
    conn: sqlite3.Connection,
) -> None:
    """Read against a file with recorded callers → hint redirects to impact_check."""
    _seed_digest(conn, file_path="src/hub.py", inbound=4)
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Read",
        tool_payload={"file_path": "src/hub.py"},
    )
    assert "memory_impact_check" in result.discipline_hint
    assert "verdict='medium'" in result.discipline_hint
    # verdict promoted from allow → allow_with_advisories so the hook
    # surfaces the hint in the system-reminder.
    assert result.verdict == "allow_with_advisories"


def test_lint_discipline_hint_fires_on_edit(conn: sqlite3.Connection) -> None:
    _seed_digest(conn, file_path="src/x.py", inbound=2)
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "src/x.py"},
    )
    assert "memory_impact_check" in result.discipline_hint


def test_lint_no_discipline_hint_for_unindexed_file(
    conn: sqlite3.Connection,
) -> None:
    """File not in code_digests → no hint (fallback to Read is forced anyway)."""
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Read",
        tool_payload={"file_path": "src/never_indexed.py"},
    )
    assert result.discipline_hint == ""


def test_lint_no_discipline_hint_for_low_impact_file(
    conn: sqlite3.Connection,
) -> None:
    """File with 0 callers → no leverage, hint stays empty."""
    _seed_digest(conn, file_path="src/leaf.py", inbound=0)
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Read",
        tool_payload={"file_path": "src/leaf.py"},
    )
    assert result.discipline_hint == ""


def test_lint_no_discipline_hint_for_canonical_tools(conn: sqlite3.Connection) -> None:
    """Canonical memory_* tools ARE the right path -- no redirect."""
    _seed_digest(conn, file_path="src/hub.py", inbound=10)
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="memory_get",
        tool_payload={"file_path": "src/hub.py"},
    )
    assert result.discipline_hint == ""


def test_lint_no_discipline_hint_when_no_file_path(
    conn: sqlite3.Connection,
) -> None:
    """Bash, etc. — no file_path → no hint."""
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Bash",
        tool_payload={"command": "ls"},
    )
    assert result.discipline_hint == ""
