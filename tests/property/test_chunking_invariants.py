"""Property tests for plain-text chunking.

Two invariants:
1. Chunks are non-overlapping and in order.
2. Reassembling the chunk texts (without overlap) produces the non-whitespace
   content of the original.
"""

from __future__ import annotations

from itertools import pairwise

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agent_memory_lite.chunking.text import chunk_text

_text_strategy = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126, blacklist_characters="\x7f"),
    min_size=0,
    max_size=2000,
).map(lambda s: s.replace("\r", ""))


@given(text=_text_strategy, max_tokens=st.integers(min_value=10, max_value=200))
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_chunks_are_non_overlapping_and_in_order(text: str, max_tokens: int) -> None:
    chunks = chunk_text(text, max_tokens=max_tokens)
    for prev, current in pairwise(chunks):
        assert prev.char_end <= current.char_start
        assert prev.line_end <= current.line_start


@given(text=_text_strategy, max_tokens=st.integers(min_value=10, max_value=200))
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_chunk_lines_are_well_formed(text: str, max_tokens: int) -> None:
    chunks = chunk_text(text, max_tokens=max_tokens)
    for chunk in chunks:
        assert chunk.line_start >= 1
        assert chunk.line_end >= chunk.line_start
        assert chunk.char_start >= 0
        assert chunk.char_end > chunk.char_start
        assert chunk.text == text[chunk.char_start : chunk.char_end]


@given(text=_text_strategy)
@settings(max_examples=50, deadline=None)
def test_concatenation_preserves_non_whitespace(text: str) -> None:
    chunks = chunk_text(text, max_tokens=100)
    if not text.strip():
        assert chunks == []
        return
    rejoined = "".join(chunk.text for chunk in chunks)
    assert "".join(rejoined.split()) == "".join(text.split())
