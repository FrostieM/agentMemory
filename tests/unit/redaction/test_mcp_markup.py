"""Tests for the MCP function-call markup guard.

Pins:
* Idempotent: clean text → unchanged.
* Strict markers only: generic angle brackets stay.
* Earliest marker wins.
* Rationale extraction recovers content from leaked
  `<parameter name="rationale">` blocks.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agent_memory_lite.redaction.mcp_markup import (
    extract_rationale,
    has_mcp_markup,
    strip_mcp_markup,
)


def test_clean_text_passes_through() -> None:
    """Operator-legitimate text without markers is untouched."""
    text = "Use VPS git-worktree releases with PM2 current symlink."
    assert strip_mcp_markup(text) == text
    assert has_mcp_markup(text) is False


def test_generic_angle_brackets_kept() -> None:
    """`<repo>`, `<=`, `<name>` etc are operator-legitimate placeholders."""
    text = (
        "MEMORY_DB_PATH points at <repo>/.agent_memory/memory.db; "
        "freshness <= 2s; insert <name> into the hook command."
    )
    assert strip_mcp_markup(text) == text
    assert has_mcp_markup(text) is False


def test_strips_decision_text_closing_tag() -> None:
    text = (
        "As of commit 51b62b0, every flag defaults ON.</decision_text>\n"
        '<parameter name="rationale">leaked rationale</parameter>'
    )
    cleaned = strip_mcp_markup(text)
    assert cleaned == "As of commit 51b62b0, every flag defaults ON."
    assert has_mcp_markup(text) is True


def test_strips_raw_text_closing_tag() -> None:
    text = "Investigated retrieval pipeline.</raw_text> <importance>0.85</importance>"
    cleaned = strip_mcp_markup(text)
    assert cleaned == "Investigated retrieval pipeline."


def test_strips_parameter_open_tag() -> None:
    text = 'Body content here.\n<parameter name="extra">leak</parameter>'
    cleaned = strip_mcp_markup(text)
    assert cleaned == "Body content here."


def test_earliest_marker_wins() -> None:
    """Multiple markers — truncate at the first one."""
    text = "prefix </parameter> middle </invoke> tail"
    cleaned = strip_mcp_markup(text)
    assert cleaned == "prefix"


def test_idempotent_on_already_cleaned() -> None:
    text = "clean already"
    once = strip_mcp_markup(text)
    twice = strip_mcp_markup(once)
    assert once == twice == text


def test_strip_handles_none_and_empty() -> None:
    assert strip_mcp_markup(None) is None
    assert strip_mcp_markup("") == ""


def test_extract_rationale_pulls_inner_text() -> None:
    text = (
        "decision body.</decision_text>\n"
        '<parameter name="rationale">The real rationale '
        "spans multiple lines.</parameter>"
    )
    assert extract_rationale(text) == "The real rationale spans multiple lines."


def test_extract_rationale_returns_none_when_absent() -> None:
    assert extract_rationale("clean text without rationale block") is None
    assert extract_rationale(None) is None
    assert extract_rationale("") is None


def test_extract_rationale_handles_unterminated_block() -> None:
    """Operator value cut off before </parameter> — take to end of input."""
    text = '<parameter name="rationale">truncated rationale missing close'
    assert extract_rationale(text) == "truncated rationale missing close"


@given(
    prefix=st.text(min_size=0, max_size=100, alphabet=st.characters(blacklist_characters="<>")),
    marker=st.sampled_from(
        ["</decision_text>", "</raw_text>", "</parameter>", "</invoke>", '<parameter name="x">']
    ),
    suffix=st.text(min_size=0, max_size=100),
)
@settings(
    max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
def test_strip_invariant_no_marker_remains(prefix: str, marker: str, suffix: str) -> None:
    """For any prefix + marker + suffix combination, output contains no marker."""
    text = prefix + marker + suffix
    cleaned = strip_mcp_markup(text)
    assert cleaned is not None
    assert "</decision_text>" not in cleaned
    assert "</raw_text>" not in cleaned
    assert "</parameter>" not in cleaned
    assert "</invoke>" not in cleaned
    assert '<parameter name="' not in cleaned


@given(text=st.text(min_size=1, max_size=200))
@settings(max_examples=50, deadline=None)
def test_strip_idempotent_property(text: str) -> None:
    """Applying strip twice equals applying once."""
    once = strip_mcp_markup(text)
    twice = strip_mcp_markup(once)
    assert once == twice
