from __future__ import annotations

from agent_memory_lite.chunking.markdown import chunk_markdown


def test_splits_on_headings() -> None:
    md = "# A\nfirst\n\n## B\nsecond\n\n# C\nthird\n"
    chunks = chunk_markdown(md)
    headings = [c.heading for c in chunks]
    assert headings == ["A", "B", "C"]


def test_no_headings_yields_one_chunk() -> None:
    md = "plain paragraph one.\n\nplain paragraph two.\n"
    chunks = chunk_markdown(md)
    assert len(chunks) == 1
    assert chunks[0].heading is None


def test_oversized_section_falls_back_to_text_chunks() -> None:
    paragraph = "word " * 200
    big = f"# H\n{paragraph}\n\n{paragraph}\n\n{paragraph}\n"
    chunks = chunk_markdown(big, max_tokens=80)
    assert len(chunks) > 1
    assert all(c.heading == "H" for c in chunks)


def test_empty_input() -> None:
    assert chunk_markdown("") == []
    assert chunk_markdown("   ") == []
