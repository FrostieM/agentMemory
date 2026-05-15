"""Unit tests for capability search helpers (token-overlap ranker primitives)."""

from __future__ import annotations

from agent_memory_lite.repositories.capabilities_search_helpers import (
    _STOP_TOKENS,
    contains_any,
    json_list,
    tokens_from,
)


def test_tokens_from_empty_returns_empty() -> None:
    assert tokens_from(None) == []
    assert tokens_from("") == []


def test_tokens_from_length_filter_skips_single_char() -> None:
    """Length-filter from original implementation: tokens shorter than 2 chars dropped."""
    out = tokens_from("a b X")
    assert out == [], "all tokens 1 char => filtered"


def test_tokens_from_lowercases() -> None:
    assert tokens_from("BackTest TIER calibration") == ["backtest", "tier", "calibration"]


def test_tokens_from_filters_generic_decoration_stop_words() -> None:
    """The SIGNAL-KILLER set: words that appear in most capability descriptions."""
    out = tokens_from("system data operator engineer design")
    # All five are decoration tokens => filtered out
    assert out == []


def test_tokens_from_filters_articles_and_pronouns() -> None:
    out = tokens_from("we should use this and that")
    # we/should/use/this/and/that are all in stop list
    assert out == []


def test_tokens_from_preserves_project_domain_terms() -> None:
    """Project-domain words must KEEP their weight (the whole point of stop-list)."""
    out = tokens_from("should we deploy tier2 calibration today without backtest")
    # Stop-list drops should/we/today/without; domain terms survive
    assert "deploy" in out
    assert "tier2" in out
    assert "calibration" in out
    assert "backtest" in out
    # Stop tokens NOT in result
    assert "should" not in out
    assert "we" not in out
    assert "today" not in out
    assert "without" not in out


def test_tokens_from_handles_punctuation() -> None:
    out = tokens_from("strategy_exit_calibrator.ts: fix tier ladders")
    assert "strategy_exit_calibrator.ts" in out
    assert "fix" in out
    assert "tier" in out
    assert "ladders" in out


def test_stop_tokens_invariants() -> None:
    """Stop-list must be a frozenset of lowercase strings and contain known offenders."""
    assert isinstance(_STOP_TOKENS, frozenset)
    for token in _STOP_TOKENS:
        assert token == token.lower(), f"{token!r} not lowercased in stop list"
    # Smoke: SIGNAL-KILLER set is present (live test 2026-05-15 motivated these).
    for offender in ("system", "data", "operator", "engineer", "design", "interface"):
        assert offender in _STOP_TOKENS


def test_stop_tokens_no_domain_signal_words() -> None:
    """Critical: project-specific terms must NOT be in the stop list."""
    for keeper in ("memory", "tier", "backtest", "deploy", "strategy", "copybot", "calibration"):
        assert keeper not in _STOP_TOKENS, f"{keeper!r} carries signal — must not be stop word"


def test_contains_any_empty_tokens_matches_all() -> None:
    assert contains_any("anything", []) is True


def test_contains_any_finds_substring() -> None:
    assert contains_any("Senior Frontend UX Architect", ["frontend"]) is True
    assert contains_any("Senior Frontend UX Architect", ["backend"]) is False


def test_contains_any_lowercases_text_only() -> None:
    """contains_any lowercases the TEXT side; callers must pre-lower tokens (tokens_from does)."""
    # Lowercased token matches mixed-case text (text is lowered internally)
    assert contains_any("Senior Frontend UX Architect", ["architect"]) is True
    # Uppercase token does NOT match — callers are required to use tokens_from output
    assert contains_any("Senior Frontend UX Architect", ["ARCHITECT"]) is False


def test_json_list_handles_none_and_empty() -> None:
    assert json_list(None) == []
    assert json_list("") == []
    assert json_list("[]") == []


def test_json_list_returns_strings() -> None:
    assert json_list('["a", 1, true]') == ["a", "1", "True"]


def test_json_list_rejects_non_list_json() -> None:
    assert json_list('{"not": "a list"}') == []
