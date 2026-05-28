"""Regression: the inline LLM extractor on the synchronous episode-write
path is bounded by ``MEMORY_LLM_EXTRACT_TIMEOUT_SEC`` ("abort after T").

A cold / slow 7B Ollama generation used to run inline with the
``OllamaExtractor`` default 30s timeout, which (stacked with a cold
embedding-model load) turned a ``memory_write`` into a multi-minute hang.
The write path must instead cap the inline extractor tightly and degrade
to "no LLM candidates" -- the fast heuristic + correction extractors
still run regardless.

Pairs with the cache-only embedding-provider regression in
``tests/unit/embeddings/test_provider_local_files_only.py``.
"""

from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.extraction.llm_extractor import OllamaExtractor
from agent_memory_lite.ingestion.auto_promote import _build_extractors


@pytest.fixture
def empty_conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _settings(timeout: str | None) -> Settings:
    kwargs: dict[str, str] = {
        "_env_file": None,  # type: ignore[dict-item]
        "OLLAMA_PROBE_SKIP": "false",  # so the OllamaExtractor IS registered
        "LLM_BACKEND": "ollama",
    }
    if timeout is not None:
        kwargs["MEMORY_LLM_EXTRACT_TIMEOUT_SEC"] = timeout
    return Settings(**kwargs)  # type: ignore[arg-type]


def test_inline_extractor_uses_configured_timeout(empty_conn: sqlite3.Connection) -> None:
    settings = _settings("3.0")
    ollama = [e for e in _build_extractors(settings, empty_conn) if isinstance(e, OllamaExtractor)]
    assert len(ollama) == 1
    assert ollama[0]._timeout == 3.0


def test_inline_timeout_defaults_to_10s(empty_conn: sqlite3.Connection) -> None:
    settings = _settings(None)
    # The default must be far below the OllamaExtractor 30s default so an
    # interactive write stays responsive.
    assert settings.llm_extract_timeout_sec == 10.0
    ollama = [e for e in _build_extractors(settings, empty_conn) if isinstance(e, OllamaExtractor)]
    assert len(ollama) == 1
    assert ollama[0]._timeout == 10.0


def test_inline_timeout_rejects_above_max() -> None:
    """A value above 120s is refused, so the 'abort after T' guard cannot
    be configured into a de-facto hang."""
    with pytest.raises(ValidationError):
        _settings("999")


def test_inline_timeout_rejects_non_positive() -> None:
    """Zero / negative is refused (gt=0), so the timeout is always a real
    deadline."""
    with pytest.raises(ValidationError):
        _settings("0")
