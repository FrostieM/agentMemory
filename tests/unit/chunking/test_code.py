from __future__ import annotations

from agent_memory_lite.chunking.code import chunk_code

PY_SOURCE = '''\
"""module docstring"""


def alpha(x):
    return x + 1


class Beta:
    def gamma(self):
        return 42


async def delta():
    return None
'''


def test_python_chunks_split_by_top_level() -> None:
    chunks = chunk_code(PY_SOURCE, language="python")
    names = [c.symbols[0] for c in chunks]
    assert names == ["alpha", "Beta", "delta"]


def test_python_chunks_carry_line_ranges() -> None:
    chunks = chunk_code(PY_SOURCE, language="python")
    for chunk in chunks:
        assert chunk.line_start <= chunk.line_end


def test_unparseable_python_falls_back() -> None:
    bad = "def broken( :\nreturn"
    chunks = chunk_code(bad, language="python")
    # falls back to plain text packer; at least one chunk emitted
    assert chunks


def test_other_language_uses_regex_symbols() -> None:
    js = "function foo() {}\nfunction bar() {}\n"
    chunks = chunk_code(js, language="javascript")
    assert chunks
    symbols = {sym for chunk in chunks for sym in chunk.symbols}
    assert "foo" in symbols
    assert "bar" in symbols


def test_empty_input_returns_empty() -> None:
    assert chunk_code("", language="python") == []
