from __future__ import annotations

from agent_memory_lite.graph.canonicalize import canonicalize_name


def test_lowercases() -> None:
    assert canonicalize_name("SQLite") == "sqlite"


def test_collapses_whitespace() -> None:
    assert canonicalize_name("  Memory   Service  ") == "memory service"


def test_strips_outer_punctuation() -> None:
    assert canonicalize_name("...SQLite!!!") == "sqlite"


def test_empty_input() -> None:
    assert canonicalize_name("") == ""
    assert canonicalize_name("   ") == ""
