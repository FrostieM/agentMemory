from __future__ import annotations

from agent_memory_lite.retrieval.normalize import normalize


def test_blank_query_yields_empty_extractions() -> None:
    n = normalize("")
    assert n.paths == []
    assert n.symbols == []
    assert n.errors == []


def test_extracts_paths() -> None:
    n = normalize("look at src/agent_memory_lite/retrieval/normalize.py")
    assert "src/agent_memory_lite/retrieval/normalize.py" in n.paths


def test_extracts_camel_case_symbols() -> None:
    n = normalize("debug HelloWorld and snake_case_func behavior")
    assert "HelloWorld" in n.symbols
    assert "snake_case_func" in n.symbols


def test_extracts_error_classes() -> None:
    n = normalize("seeing RuntimeError and CustomConfigException in logs")
    assert "RuntimeError" in n.errors
    assert "CustomConfigException" in n.errors


def test_dedupes_paths_and_errors() -> None:
    n = normalize("RuntimeError again, RuntimeError and src/a/b.py vs src/a/b.py")
    assert n.errors.count("RuntimeError") == 1
    assert n.paths.count("src/a/b.py") == 1
