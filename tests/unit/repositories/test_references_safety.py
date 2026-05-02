"""``find_references`` safety regressions.

The reverse-lookup uses LIKE scans across many text columns. Make
sure a malicious / weird ``target_id`` cannot blow up the scan or
match unintended rows.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.repositories.references_repo import find_references


def _seed_decision(
    conn: sqlite3.Connection, *, decision_id: str, workspace_id: str, text: str
) -> None:
    conn.execute(
        """
        INSERT INTO decisions (
            id, workspace_id, title, decision_text, rationale, status,
            valid_from, created_at, updated_at, importance, confidence,
            source_episode_id, supersedes_decision_id, valid_to
        ) VALUES (?, ?, 'T', ?, 'r', 'active', '2026-01-01', '2026-01-01',
                  '2026-01-01', 0.5, 0.9, NULL, NULL, NULL)
        """,
        (decision_id, workspace_id, text),
    )
    conn.commit()


def test_find_references_rejects_short_or_empty_target(
    applied_conn: sqlite3.Connection,
) -> None:
    workspace = "ref-safety-ws"
    _seed_decision(applied_conn, decision_id="dec_1", workspace_id=workspace, text="hi")
    assert find_references(applied_conn, workspace_id=workspace, target_id="") == []
    assert find_references(applied_conn, workspace_id=workspace, target_id="  ") == []
    # Two characters is below the minimum length so the scan refuses
    # rather than producing a near-global ``LIKE %ab%`` match.
    assert find_references(applied_conn, workspace_id=workspace, target_id="ab") == []


def test_find_references_escapes_like_wildcards(applied_conn: sqlite3.Connection) -> None:
    workspace = "ref-safety-ws-esc"
    # Two decisions: one mentions the literal target id, the other
    # mentions a string that would match a non-escaped LIKE pattern.
    _seed_decision(
        applied_conn,
        decision_id="dec_literal",
        workspace_id=workspace,
        text="references dec_target_xyz here",
    )
    _seed_decision(
        applied_conn,
        decision_id="dec_other",
        workspace_id=workspace,
        text="this row mentions dec_target_zzz which differs by suffix",
    )

    # If the underscore was treated as a LIKE wildcard, the search
    # for "dec_target_xyz" would also match "dec_target_zzz". With
    # escaping the underscore is a literal character so only
    # ``dec_literal`` matches.
    hits = find_references(applied_conn, workspace_id=workspace, target_id="dec_target_xyz")
    matched_ids = {hit.id for hit in hits}
    assert "dec_literal" in matched_ids
    assert "dec_other" not in matched_ids
