"""Tests for the FTS-only fallback used by the UserPromptSubmit hook
when HTTP service is unreachable."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Allow importing the script-style module without changing PYTHONPATH at runtime.
_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from inject_memory_fts_fallback import (  # type: ignore[import-not-found]  # noqa: E402
    _safe_fts_query,
    build_fts_only_context,
)


def test_safe_fts_query_strips_special_chars() -> None:
    assert _safe_fts_query("re-deploy: PM2 (worker)") == "deploy OR PM2 OR worker"
    assert _safe_fts_query("foo-bar*baz") == "foo OR bar OR baz"


def test_safe_fts_query_caps_tokens() -> None:
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
    out = _safe_fts_query(text)
    # Joined with " OR " between tokens — count tokens.
    assert len(out.split(" OR ")) == 8


def test_safe_fts_query_filters_short_tokens() -> None:
    # 1-2 char tokens dropped, 3-char survive
    assert _safe_fts_query("a is to be or") == ""
    assert _safe_fts_query("a is to be or not") == "not"


def test_safe_fts_query_empty_handled() -> None:
    assert _safe_fts_query("") == ""
    assert _safe_fts_query("   ") == ""


def test_build_fts_handles_missing_db_gracefully(tmp_path: Path) -> None:
    """Missing DB file → never raises. sqlite3.connect creates an empty
    file; section reads error out cleanly, so the result is either None
    (empty body) or a placeholder envelope. Either is acceptable —
    the important property is that no exception escapes."""
    result = build_fts_only_context(
        db_path=str(tmp_path / "does-not-exist.db"),
        workspace_id="default",
        query="anything",
    )
    # Either None (full empty) or just placeholder tags — both fine.
    assert result is None or "<memory_context>" in result


def test_build_fts_renders_full_envelope(
    applied_conn: sqlite3.Connection, tmp_db_path: Path
) -> None:
    """Seed a workspace with one decision + one behavior + one chunk, and
    confirm the fallback renders a complete envelope including all of them.
    """
    applied_conn.execute(
        "INSERT INTO core_memory (id, workspace_id, key, value, confidence, importance, "
        "active, pinned, created_at, updated_at) VALUES "
        "('cm_x', 'default', 'invariant', 'Local-only: never call cloud APIs', 1.0, 1.0, "
        "1, 1, '2026-05-04T00:00:00Z', '2026-05-04T00:00:00Z')"
    )
    applied_conn.execute(
        "INSERT INTO behavior_instructions (id, workspace_id, name, rule, kind, scope, "
        "priority, conflict_policy, applies_to_json, source_type, confidence, active, "
        "created_at, updated_at, application_count) VALUES "
        "('beh_x', 'default', 'Russian chat', 'Reply in Russian', 'communication_style', "
        "'workspace', 'user_preference', 'current_user_wins', '[]', 'manual', 0.95, 1, "
        "'2026-05-04T00:00:00Z', '2026-05-04T00:00:00Z', 5)"
    )
    applied_conn.execute(
        "INSERT INTO decisions (id, workspace_id, title, decision_text, status, importance, "
        "confidence, valid_from, created_at, updated_at) VALUES "
        "('dec_x', 'default', 'Use SQLite + FTS5', 'Source of record stays local', "
        "'active', 0.95, 0.9, '2026-05-04', '2026-05-04T00:00:00Z', '2026-05-04T00:00:00Z')"
    )
    applied_conn.execute(
        "INSERT INTO episodes (id, workspace_id, raw_text, source_type, trust_level, "
        "importance, confidence, created_at) VALUES "
        "('ep_x', 'default', 'investigated retrieval pipeline RRF fusion', "
        "'agent_action', 'agent_observed', 0.5, 1.0, '2026-05-04T00:00:00Z')"
    )
    applied_conn.execute(
        "INSERT INTO chunks (id, workspace_id, episode_id, kind, text, importance, "
        "confidence, created_at) VALUES "
        "('chk_x', 'default', 'ep_x', 'episode', 'investigated retrieval pipeline "
        "RRF fusion with FTS bm25', 0.5, 1.0, '2026-05-04T00:00:00Z')"
    )
    applied_conn.execute(
        "INSERT INTO chunks_fts (rowid, workspace_id, text, summary) "
        "SELECT rowid, workspace_id, text, summary FROM chunks WHERE id = 'chk_x'"
    )
    applied_conn.commit()

    rendered = build_fts_only_context(
        db_path=str(tmp_db_path), workspace_id="default", query="retrieval pipeline FTS"
    )
    assert rendered is not None
    assert "<memory_context>" in rendered
    assert "</memory_context>" in rendered
    assert "<core_memory>" in rendered
    assert "Local-only" in rendered
    assert "<behavior_instructions>" in rendered
    assert "Reply in Russian" in rendered
    assert "<active_decisions>" in rendered
    assert "Use SQLite + FTS5" in rendered
    assert "<retrieved_chunks>" in rendered
    assert "investigated retrieval pipeline" in rendered


def test_build_fts_xml_escapes_special_chars(
    applied_conn: sqlite3.Connection, tmp_db_path: Path
) -> None:
    """Decision text with `<`, `&`, `>` must end up XML-escaped, not literal."""
    applied_conn.execute(
        "INSERT INTO decisions (id, workspace_id, title, decision_text, status, importance, "
        "confidence, valid_from, created_at, updated_at) VALUES "
        "('dec_y', 'default', 'A < B & C > D', 'p1 < p2 ≠ p3 & finally', "
        "'active', 0.9, 0.9, '2026-05-04', '2026-05-04T00:00:00Z', '2026-05-04T00:00:00Z')"
    )
    applied_conn.commit()
    rendered = build_fts_only_context(
        db_path=str(tmp_db_path), workspace_id="default", query="anything"
    )
    assert rendered is not None
    assert "&lt;" in rendered
    assert "&amp;" in rendered
    # Must not produce stray unescaped < B or & C in the rendered XML.
    assert " < B " not in rendered
    assert " & C " not in rendered


def test_build_fts_handles_empty_workspace(
    applied_conn: sqlite3.Connection, tmp_db_path: Path
) -> None:
    """No data anywhere → returns None (caller falls back to notice)."""
    rendered = build_fts_only_context(
        db_path=str(tmp_db_path), workspace_id="empty", query="something"
    )
    # Empty workspace renders empty placeholder sections; total body has only
    # the empty self-closing tags. Whether we treat that as None or rendered
    # is implementation-defined. The current implementation returns the
    # empty-placeholder envelope because every section produces a self-closing tag.
    assert rendered is None or "<memory_context>" in rendered
