"""Unit tests for the magic-number mechanical detector."""

from __future__ import annotations

from agent_memory_lite.enforcement.mechanical_magic_number import (
    detect_magic_number,
)


def test_strategy_path_with_magic_threshold_fires() -> None:
    diff = "if confidence > 0.85:\n    do_thing()\n"
    out = detect_magic_number(diff, "src/strategy/calibrator.py")
    assert out is not None
    assert "0.85" in out
    assert "confidence" in out


def test_strategy_path_with_neutral_literal_passes() -> None:
    diff = "if confidence > 0:\n    return\n"
    assert detect_magic_number(diff, "src/strategy/calibrator.py") is None


def test_non_strategy_path_passes_even_with_magic() -> None:
    """Outside strategy/calibrator paths the rule does not enforce."""
    diff = "if confidence > 0.85:\n    return\n"
    assert detect_magic_number(diff, "src/api/health.py") is None


def test_calibrator_path_fires() -> None:
    diff = "if tier_ratio >= 0.72:\n    pass\n"
    assert detect_magic_number(diff, "src/calibrator/tier_ladder.py") is not None


def test_identifier_without_threshold_name_passes() -> None:
    """Numeric comparison on unrelated identifier name does not fire."""
    diff = "if count > 42:\n    return\n"
    assert detect_magic_number(diff, "src/strategy/calibrator.py") is None


def test_named_constant_in_same_diff_releases_check() -> None:
    """Agent extracted to UPPER_SNAKE constant — skip violation."""
    diff = "MIN_CONFIDENCE = 0.85\nif confidence > MIN_CONFIDENCE:\n    return\n"
    assert detect_magic_number(diff, "src/strategy/calibrator.py") is None


def test_tier_edge_paths_match() -> None:
    diff = "if weight > 0.7:\n    return\n"
    assert detect_magic_number(diff, "src/edge_signals/router.py") is not None


def test_windows_separators_normalized() -> None:
    diff = "if margin >= 0.65:\n    return\n"
    assert detect_magic_number(diff, r"src\strategy\fast_path.py") is not None


def test_score_threshold_pattern() -> None:
    diff = "if score < 0.42:\n    return\n"
    assert detect_magic_number(diff, "src/strategies/runner.py") is not None


def test_multiple_lines_first_match_returned() -> None:
    diff = "if confidence > 0.85:\n    pass\nif threshold < 0.3:\n    pass\n"
    out = detect_magic_number(diff, "src/strategy/calibrator.py")
    assert out is not None
    assert "0.85" in out  # first match in source order
