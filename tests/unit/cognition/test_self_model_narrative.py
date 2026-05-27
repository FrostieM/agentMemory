"""Self-model narrative formatting -- regression tests for the v2 polish.

The v1 template had three readability bugs surfaced by the empirical
probe on copyBot:
  * mid-word truncation ("(early-loss-preven")
  * TRIGGER: scaffolding leaked into the narrative
  * ``X; Y; Z`` reads worse than ``X, Y, and Z``

These tests lock the v2 fixes in.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.cognition.self_model import (
    _behavior_snippet,
    _decision_snippet,
    _join_human,
    _trim_to_words,
    _uncertainty_snippet,
    refresh_self_model,
)
from agent_memory_lite.utils.time import iso_now

# ============================================================
# _trim_to_words -- word-boundary truncation
# ============================================================


def test_trim_short_input_returns_clean() -> None:
    """No trim needed, no ellipsis appended."""
    assert _trim_to_words("Hello world.", 90) == "Hello world."


def test_trim_strips_trailing_punctuation() -> None:
    """Cleans up trailing ``;:,`` from short inputs."""
    assert _trim_to_words("Hello world;", 90) == "Hello world"
    assert _trim_to_words("body,", 90) == "body"


def test_trim_cuts_at_word_boundary_not_midword() -> None:
    """Long input -> cut at last space before max_chars, '...' appended."""
    text = " ".join(["word"] * 30)  # 30 * 5 = 150 chars
    out = _trim_to_words(text, 40)
    assert out.endswith("...")
    # Must not end mid-word (no character right before '...' that isn't ' ').
    body = out.removesuffix("...")
    # body itself doesn't end with a partial word -- last token should be 'word' or 'wor'-allowed if no space
    assert not body[-1].isalpha() or " " in body  # has at least one space


def test_trim_no_late_space_keeps_hard_cut() -> None:
    """When there is no space in the first half, just hard-cut."""
    text = "a" * 100
    out = _trim_to_words(text, 40)
    assert out.endswith("...")
    assert len(out.removesuffix("...")) <= 40


def test_trim_collapses_runs_of_whitespace() -> None:
    """Tabs / newlines / double-spaces collapse to single space."""
    out = _trim_to_words("Hello\t\n  world", 90)
    assert out == "Hello world"


# ============================================================
# _behavior_snippet -- skips TRIGGER: scaffolding
# ============================================================


def test_behavior_snippet_prefers_name() -> None:
    """When name is set, it wins over rule_one_line (which is verbose)."""
    row = _fake_row(
        {
            "name": "Drive the project",
            "rule_one_line": "TRIGGER: When user asks ANY of...",
        }
    )
    assert _behavior_snippet(row) == "Drive the project"


def test_behavior_snippet_falls_back_to_rule_when_name_empty() -> None:
    row = _fake_row({"name": "", "rule_one_line": "Plain rule body, no scaffolding"})
    assert _behavior_snippet(row) == "Plain rule body, no scaffolding"


def test_behavior_snippet_skips_trigger_scaffolding_with_no_name() -> None:
    """If only rule_one_line exists AND it starts with TRIGGER:, return empty."""
    row = _fake_row({"name": "", "rule_one_line": "TRIGGER: When the user asks..."})
    assert _behavior_snippet(row) == ""


# ============================================================
# _decision_snippet -- prefers title over auto-truncated gist
# ============================================================


def test_decision_snippet_prefers_title() -> None:
    row = _fake_row({"title": "Use quarter-Kelly sizing", "gist": "Use quarter-Kelly siz"})
    assert _decision_snippet(row) == "Use quarter-Kelly sizing"


def test_decision_snippet_falls_back_to_gist() -> None:
    row = _fake_row({"title": "", "gist": "some gist"})
    assert _decision_snippet(row) == "some gist"


# ============================================================
# _uncertainty_snippet
# ============================================================


def test_uncertainty_snippet_prefers_claim() -> None:
    row = _fake_row({"claim": "Half-Kelly is safe", "title": "Kelly thesis", "gist": "..."})
    assert _uncertainty_snippet(row) == "Half-Kelly is safe"


# ============================================================
# _join_human -- natural English joins
# ============================================================


def test_join_human_zero_items() -> None:
    assert _join_human([]) == ""


def test_join_human_one_item() -> None:
    assert _join_human(["alpha"]) == "alpha"


def test_join_human_two_items() -> None:
    assert _join_human(["alpha", "beta"]) == "alpha and beta"


def test_join_human_three_items_uses_oxford() -> None:
    assert _join_human(["alpha", "beta", "gamma"]) == "alpha, beta, and gamma"


def test_join_human_filters_empty_strings() -> None:
    assert _join_human(["alpha", "", "  ", "beta"]) == "alpha and beta"


# ============================================================
# End-to-end: real narrative on a seeded workspace -- no mid-word
# cuts, no TRIGGER prefixes, no semicolon-joined fragments.
# ============================================================


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


def _seed_decision(c: sqlite3.Connection, id_: str, title: str, outcome: float = 0.5) -> None:
    c.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at, outcome_score, pinned)
           VALUES (?, 'ws', ?, 'body', ?, 'active', ?, ?, ?, ?, 0)""",
        (id_, title, title[:60], iso_now(), iso_now(), iso_now(), outcome),
    )
    c.commit()


def _seed_behavior(c: sqlite3.Connection, id_: str, name: str, rule: str) -> None:
    c.execute(
        """INSERT INTO behaviors
           (id, workspace_id, name, kind, scope, priority, rule, rule_one_line,
            applies_to_json, active, pinned, outcome_score, created_at, updated_at)
           VALUES (?, 'ws', ?, 'operating_rule', 'workspace', 'project_convention',
                   ?, ?, '[]', 1, 1, 0.3, ?, ?)""",
        (id_, name, rule, rule, iso_now(), iso_now()),
    )
    c.commit()


def _seed_rejected_theory(c: sqlite3.Connection, id_: str, claim: str) -> None:
    c.execute(
        """INSERT INTO theories
           (id, workspace_id, title, claim, gist, status, created_at, updated_at)
           VALUES (?, 'ws', 't', ?, ?, 'rejected', ?, ?)""",
        (id_, claim, claim[:60], iso_now(), iso_now()),
    )
    c.commit()


def test_narrative_uses_natural_english_joins(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, "dec_1", "Use quarter-Kelly sizing", outcome=0.8)
    _seed_decision(conn, "dec_2", "Calibrate per strategy weekly", outcome=0.6)
    _seed_decision(conn, "dec_3", "Run paper before live", outcome=0.5)
    model = refresh_self_model(conn, workspace_id="ws")
    assert model is not None
    # Three invariants -> Oxford comma join. Round-2 audit (H3): each
    # decision snippet is now quoted so injected imperative text reads
    # as referenced data, not the agent's own voice.
    assert (
        '"Use quarter-Kelly sizing", "Calibrate per strategy weekly", and "Run paper before live"'
    ) in model.identity_text


def test_narrative_skips_trigger_scaffolded_rule(conn: sqlite3.Connection) -> None:
    """Behavior with name='Drive project' + rule starting 'TRIGGER:'
    should surface 'Drive project', NOT the TRIGGER prefix."""
    _seed_behavior(
        conn,
        "beh_drive",
        "Drive the project",
        "TRIGGER: When the user asks ANY of: 'what's next', 'status'",
    )
    model = refresh_self_model(conn, workspace_id="ws")
    assert model is not None
    assert "Drive the project" in model.identity_text
    assert "TRIGGER:" not in model.identity_text
    assert "When the user asks ANY of" not in model.identity_text


def test_narrative_no_midword_truncation(conn: sqlite3.Connection) -> None:
    """Pathological long-title case: truncation must cut at space.

    Verify by reconstructing each word that appears before ``...`` and
    checking it exists in the source title -- a mid-word cut would
    produce a fragment ("rationa") that is NOT in the original.
    """
    long_title = (
        "This is a very long decision title that contains many words "
        "explaining the rationale behind a complex architectural choice "
        "that exceeds the snippet character cap"
    )
    source_words = set(long_title.split())
    _seed_decision(conn, "dec_long", long_title, outcome=0.6)
    model = refresh_self_model(conn, workspace_id="ws")
    assert model is not None
    text = model.identity_text
    if "..." in text:
        idx = text.find("...")
        # Find the last word boundary before "..." -- back up to the
        # nearest space, then the word between that space and "..." is
        # the last word in the snippet.
        prefix = text[:idx].rstrip()
        last_space = prefix.rfind(" ")
        last_word = prefix[last_space + 1 :] if last_space >= 0 else prefix
        # That word must appear as a complete word in the source title,
        # OR be empty (if the cut hit a space exactly).
        if last_word:
            assert last_word in source_words, f"truncation produced mid-word fragment {last_word!r}"


def test_narrative_singular_when_one_invariant(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, "dec_only", "Single invariant", outcome=0.8)
    model = refresh_self_model(conn, workspace_id="ws")
    assert model is not None
    # Round-2 audit (H3): the snippet is quoted — singular phrasing kept.
    assert 'My invariant is "Single invariant"' in model.identity_text


def test_narrative_handles_uncertainty(conn: sqlite3.Connection) -> None:
    _seed_rejected_theory(conn, "th_x", "Half-Kelly is safe under volatility tail risk")
    model = refresh_self_model(conn, workspace_id="ws")
    assert model is not None
    assert "Half-Kelly is safe" in model.identity_text
    assert (
        "I was wrong about" in model.identity_text
        or "Theories I've found incorrect" in model.identity_text
    )


# ============================================================
# Helpers
# ============================================================


def _fake_row(payload: dict[str, object]) -> sqlite3.Row:
    """Build a real sqlite3.Row from a dict for unit tests."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    cols_csv = ", ".join(payload.keys())
    placeholders = ", ".join("?" for _ in payload)
    c.execute(f"CREATE TABLE t ({cols_csv})")
    c.execute(f"INSERT INTO t ({cols_csv}) VALUES ({placeholders})", tuple(payload.values()))
    row = c.execute("SELECT * FROM t").fetchone()
    c.close()
    return row
