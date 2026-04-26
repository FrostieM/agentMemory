"""Text / code / log chunking. Phase 1 ships text + line_ranges only."""

from agent_memory_lite.chunking.line_ranges import (
    char_to_line,
    line_starts,
    span_to_line_range,
)
from agent_memory_lite.chunking.text import TextChunk, chunk_text

__all__ = [
    "TextChunk",
    "char_to_line",
    "chunk_text",
    "line_starts",
    "span_to_line_range",
]
