from __future__ import annotations

from agent_memory_lite.chunking.line_ranges import (
    char_to_line,
    line_starts,
    span_to_line_range,
)


def test_line_starts_simple() -> None:
    assert line_starts("a\nb\nc") == [0, 2, 4]


def test_line_starts_trailing_newline_ignored() -> None:
    assert line_starts("a\nb\n") == [0, 2]


def test_char_to_line_offset_in_first_line() -> None:
    starts = line_starts("hello\nworld")
    assert char_to_line(starts, 0) == 1
    assert char_to_line(starts, 4) == 1


def test_char_to_line_offset_in_second_line() -> None:
    starts = line_starts("hello\nworld")
    assert char_to_line(starts, 6) == 2
    assert char_to_line(starts, 10) == 2


def test_span_to_line_range_single_line() -> None:
    text = "first line\nsecond line"
    assert span_to_line_range(text, 0, 5) == (1, 1)


def test_span_to_line_range_multi_line() -> None:
    text = "alpha\nbeta\ngamma"
    assert span_to_line_range(text, 0, len(text)) == (1, 3)


def test_span_to_line_range_negative_offset_clamps_to_one() -> None:
    text = "a\nb"
    assert char_to_line(line_starts(text), -5) == 1
