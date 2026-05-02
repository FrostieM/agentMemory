"""Plain-text chunking by paragraph with a token cap.

Algorithm:
1. Split on blank-line paragraph boundaries, keeping the original text exactly.
2. Greedily pack paragraphs into chunks of at most `max_tokens` tokens.
3. If a single paragraph exceeds `max_tokens`, fall back to splitting it on
   sentence boundaries (`.`, `!`, `?` followed by whitespace).
4. Optional overlap copies the last `overlap_tokens` worth of text at the head
   of the next chunk; lines monotonically increase regardless.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agent_memory_lite.chunking.line_ranges import span_to_line_range
from agent_memory_lite.utils.tokens import estimate_tokens

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

DEFAULT_MAX_TOKENS = 600
DEFAULT_OVERLAP_TOKENS = 0


@dataclass(frozen=True, slots=True)
class TextChunk:
    text: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int


def _segments(text: str, pattern: re.Pattern[str]) -> list[tuple[int, int]]:
    """Return (start, end) offsets for each non-empty segment between separators."""
    segments: list[tuple[int, int]] = []
    cursor = 0
    for sep in pattern.finditer(text):
        if sep.start() > cursor:
            segments.append((cursor, sep.start()))
        cursor = sep.end()
    if cursor < len(text):
        segments.append((cursor, len(text)))
    return [(s, e) for s, e in segments if text[s:e].strip()]


def _explode_long_segment(text: str, start: int, end: int) -> list[tuple[int, int]]:
    sub_segments = _segments(text[start:end], _SENTENCE_SPLIT)
    return [(start + s, start + e) for s, e in sub_segments] or [(start, end)]


def chunk_text(
    text: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[TextChunk]:
    if not text.strip():
        return []
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be in [0, max_tokens)")

    base = _segments(text, _PARAGRAPH_SPLIT)
    flat: list[tuple[int, int]] = []
    for start, end in base:
        if estimate_tokens(text[start:end]) > max_tokens:
            flat.extend(_explode_long_segment(text, start, end))
        else:
            flat.append((start, end))

    chunks: list[TextChunk] = []
    current: list[tuple[int, int]] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if not current:
            return
        cs = current[0][0]
        ce = current[-1][1]
        chunk_text_str = text[cs:ce]
        ls, le = span_to_line_range(text, cs, ce)
        chunks.append(
            TextChunk(text=chunk_text_str, char_start=cs, char_end=ce, line_start=ls, line_end=le)
        )
        if overlap_tokens > 0 and current:
            tail_tokens = 0
            tail: list[tuple[int, int]] = []
            for seg in reversed(current):
                tail.insert(0, seg)
                tail_tokens += estimate_tokens(text[seg[0] : seg[1]])
                if tail_tokens >= overlap_tokens:
                    break
            current = list(tail)
            current_tokens = tail_tokens
        else:
            current = []
            current_tokens = 0

    for start, end in flat:
        seg_tokens = estimate_tokens(text[start:end])
        if current and current_tokens + seg_tokens > max_tokens:
            flush()
        current.append((start, end))
        current_tokens += seg_tokens

    flush()
    return chunks
