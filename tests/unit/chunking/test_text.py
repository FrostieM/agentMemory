from __future__ import annotations

from itertools import pairwise

import pytest

from agent_memory_lite.chunking.text import chunk_text


def test_empty_text_returns_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n  \n") == []


def test_single_short_paragraph_one_chunk() -> None:
    chunks = chunk_text("Hello there.")
    assert len(chunks) == 1
    assert chunks[0].text == "Hello there."
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 1


def test_two_paragraphs_split_when_over_budget() -> None:
    long_para = "word " * 200
    text = f"{long_para}\n\n{long_para}"
    chunks = chunk_text(text, max_tokens=80)
    assert len(chunks) >= 2
    assert all(c.line_start <= c.line_end for c in chunks)


def test_chunks_are_in_order_with_monotonic_lines() -> None:
    paragraphs = "\n\n".join(f"Paragraph {i}: " + ("word " * 30) for i in range(6))
    chunks = chunk_text(paragraphs, max_tokens=60)
    for prev, current in pairwise(chunks):
        assert prev.char_end <= current.char_start
        assert prev.line_end <= current.line_start


def test_max_tokens_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_tokens must be positive"):
        chunk_text("hello", max_tokens=0)


def test_overlap_tokens_must_be_under_max() -> None:
    with pytest.raises(ValueError, match=r"overlap_tokens must be in \[0, max_tokens\)"):
        chunk_text("hello", max_tokens=10, overlap_tokens=10)


def test_huge_paragraph_exploded_by_sentences() -> None:
    text = " ".join(f"Sentence number {i}." for i in range(100))
    chunks = chunk_text(text, max_tokens=40)
    assert len(chunks) >= 2
    rejoined = "".join(c.text for c in chunks)
    for i in range(0, 100, 10):
        assert f"Sentence number {i}." in rejoined
