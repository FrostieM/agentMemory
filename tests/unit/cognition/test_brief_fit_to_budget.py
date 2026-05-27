"""Brief: fit_to_budget skips oversized lines and keeps going.

Earlier semantics broke on first overflow, which silently nuked every
subsequent line in the section. New semantics: skip the line that
doesn't fit and try the next."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.cognition.brief import (
    _SELF_MODEL_BRIEF_WORDS,
    compose_brief,
    fit_to_budget,
)
from agent_memory_lite.utils.time import iso_now

# ============================================================
# fit_to_budget — pure unit
# ============================================================


def test_fits_when_total_under_budget() -> None:
    out = fit_to_budget(["one two", "three four", "five"], budget=10)
    assert out == ["one two", "three four", "five"]


def test_drops_first_oversized_line_but_keeps_short_tail() -> None:
    """The earlier `break` semantics dropped the tail too; we now `continue`."""
    big = " ".join(f"w{i}" for i in range(20))  # 20 words
    out = fit_to_budget([big, "short tail"], budget=5)
    assert out == ["short tail"]


def test_drops_middle_oversized_line() -> None:
    """Middle overflow: short-before + short-after both kept."""
    big = " ".join(f"w{i}" for i in range(20))
    out = fit_to_budget(["start one", big, "end two"], budget=5)
    assert out == ["start one", "end two"]


def test_empty_input_returns_empty() -> None:
    assert fit_to_budget([], budget=10) == []


def test_zero_budget_returns_empty() -> None:
    assert fit_to_budget(["any line"], budget=0) == []


# ============================================================
# Brief identity: self-model line is capped to _SELF_MODEL_BRIEF_WORDS
# so workspace-overview + discipline lines still fit
# ============================================================


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
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def _seed_long_self_model(conn: sqlite3.Connection) -> None:
    """Insert a 95-word identity_text matching the production case."""
    long_text = " ".join(["narrative"] * 95)
    conn.execute(
        """INSERT INTO self_model
           (workspace_id, identity_text, refreshed_via, coverage_score,
            created_at, updated_at)
           VALUES ('ws', ?, 'heuristic', 0.5, ?, ?)""",
        (long_text, iso_now(), iso_now()),
    )
    conn.commit()


def test_brief_caps_long_self_model_to_brief_words(conn: sqlite3.Connection) -> None:
    _seed_long_self_model(conn)
    brief = compose_brief(conn, workspace_id="ws")
    # The capped self-model line ends with "..." (suffix marker).
    assert "..." in brief.body_md
    # The line count after the title should include both the snippet
    # and the workspace-overview line -- proving the earlier break-bug
    # is gone.
    lines = [line for line in brief.body_md.splitlines() if line.strip()]
    # Order: # ws, <self-model snippet>, ## Pinned behaviors (or similar)
    assert lines[0] == "# ws"
    assert "narrative" in lines[1]  # snippet is the second line
    # Snippet must be <= cap words + the "..." token
    snippet_words = lines[1].rstrip(".").split()
    assert len(snippet_words) <= _SELF_MODEL_BRIEF_WORDS


def test_workspace_overview_still_renders_when_self_model_long(
    conn: sqlite3.Connection,
) -> None:
    """The regression that motivated this fix: long self-model line
    dropped the workspace-overview line too."""
    _seed_long_self_model(conn)
    # Seed a pinned behavior so the workspace-overview counts are non-zero.
    conn.execute(
        """INSERT INTO behaviors
           (id, workspace_id, name, kind, scope, priority, rule, rule_one_line,
            applies_to_json, active, pinned, created_at, updated_at)
           VALUES ('beh_x', 'ws', 'beh', 'operating_rule', 'workspace',
                   'project_convention', 'do x', 'do x', '[]', 1, 1, ?, ?)""",
        (iso_now(), iso_now()),
    )
    conn.commit()
    brief = compose_brief(conn, workspace_id="ws")
    assert "Workspace overview" in brief.body_md


def test_short_self_model_not_truncated(conn: sqlite3.Connection) -> None:
    """A narrative under the cap renders as-is, no '...' suffix."""
    short_text = "I work on ws. Short test narrative."
    conn.execute(
        """INSERT INTO self_model
           (workspace_id, identity_text, refreshed_via, coverage_score,
            created_at, updated_at)
           VALUES ('ws', ?, 'heuristic', 0.5, ?, ?)""",
        (short_text, iso_now(), iso_now()),
    )
    conn.commit()
    brief = compose_brief(conn, workspace_id="ws")
    lines = [line for line in brief.body_md.splitlines() if line.strip()]
    assert lines[1] == short_text
    # No trailing ellipsis on a fits-within-cap narrative.
    assert not lines[1].endswith("...")
