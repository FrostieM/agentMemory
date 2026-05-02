from __future__ import annotations

from agent_memory_lite.chunking.symbols import extract_symbols


def test_python_extraction_via_ast() -> None:
    source = "def hello():\n    return 1\n\nclass World:\n    pass\n"
    assert extract_symbols(source, language="python") == ["hello", "World"]


def test_python_falls_back_on_syntax_error() -> None:
    bad = "def broken(:\n"
    syms = extract_symbols(bad, language="python")
    # fallback yields whatever the regex finds (could be empty if pattern not matched)
    assert isinstance(syms, list)


def test_regex_handles_export_function() -> None:
    js = "export function foo() {}\nexport class Bar {}\n"
    assert "foo" in extract_symbols(js, language="javascript")
    assert "Bar" in extract_symbols(js, language="javascript")


def test_empty_input() -> None:
    assert extract_symbols("", language="python") == []
