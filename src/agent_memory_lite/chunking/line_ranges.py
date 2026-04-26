"""Char-to-line mapping helpers.

These map character offsets within a text into 1-indexed line numbers — the
format we store in `chunks.line_start` / `chunks.line_end`.
"""

from __future__ import annotations

from bisect import bisect_right


def line_starts(text: str) -> list[int]:
    """Return the character offset of the first char of every line.

    Line 1 starts at offset 0. A trailing newline does NOT add a phantom line.
    """
    starts = [0]
    for idx, ch in enumerate(text):
        if ch == "\n" and idx + 1 < len(text):
            starts.append(idx + 1)
    return starts


def char_to_line(starts: list[int], offset: int) -> int:
    """Return the 1-indexed line number containing `offset`."""
    if offset < 0:
        return 1
    return max(1, bisect_right(starts, offset))


def span_to_line_range(text: str, start: int, end: int) -> tuple[int, int]:
    """Return (line_start, line_end) inclusive 1-indexed lines covering [start, end)."""
    starts = line_starts(text)
    line_start = char_to_line(starts, start)
    last_char = max(start, end - 1)
    line_end = char_to_line(starts, last_char)
    return line_start, line_end
