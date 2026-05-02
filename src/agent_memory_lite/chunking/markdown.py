"""Markdown chunking by heading.

Sections start at a heading (# ...) and run until the next heading of the same
or higher level. Sections that exceed the token cap fall back to the plain
text packer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agent_memory_lite.chunking.line_ranges import span_to_line_range
from agent_memory_lite.chunking.text import TextChunk, chunk_text
from agent_memory_lite.utils.tokens import estimate_tokens

DEFAULT_MAX_TOKENS = 800
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class MarkdownChunk:
    text: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    heading: str | None


def _split_sections(text: str) -> list[tuple[int, int, str | None]]:
    headings = list(_HEADING_RE.finditer(text))
    if not headings:
        return [(0, len(text), None)]
    sections: list[tuple[int, int, str | None]] = []
    if headings[0].start() > 0:
        leading = text[: headings[0].start()]
        if leading.strip():
            sections.append((0, headings[0].start(), None))
    for idx, match in enumerate(headings):
        start = match.start()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        sections.append((start, end, match.group(2)))
    return sections


def _explode(text: str, start: int, end: int, *, max_tokens: int) -> list[TextChunk]:
    return chunk_text(text[start:end], max_tokens=max_tokens)


def chunk_markdown(text: str, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> list[MarkdownChunk]:
    if not text.strip():
        return []
    chunks: list[MarkdownChunk] = []
    for start, end, heading in _split_sections(text):
        body = text[start:end]
        if not body.strip():
            continue
        if estimate_tokens(body) <= max_tokens:
            ls, le = span_to_line_range(text, start, end)
            chunks.append(
                MarkdownChunk(
                    text=body,
                    char_start=start,
                    char_end=end,
                    line_start=ls,
                    line_end=le,
                    heading=heading,
                )
            )
            continue
        for piece in _explode(text, start, end, max_tokens=max_tokens):
            chunks.append(
                MarkdownChunk(
                    text=piece.text,
                    char_start=start + piece.char_start,
                    char_end=start + piece.char_end,
                    line_start=piece.line_start,
                    line_end=piece.line_end,
                    heading=heading,
                )
            )
    return chunks
