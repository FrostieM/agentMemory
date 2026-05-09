"""2.1.3: unit tests for the LLM narrative module."""

from __future__ import annotations

from unittest.mock import patch

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.extraction.file_narrative_llm import (
    _clean_response,
    build_prompt,
    call_llm_narrative,
)


def test_build_prompt_includes_path_and_symbols() -> None:
    p = build_prompt(
        file_path="src/m.py",
        language="python",
        qualified_names=["foo", "Bar", "Bar.baz"],
        inbound_targets=["caller_a"],
        outbound_targets=["helpers.run"],
    )
    assert "src/m.py" in p
    assert "python" in p
    assert "foo, Bar, Bar.baz" in p
    assert "caller_a" in p
    assert "helpers.run" in p


def test_build_prompt_truncates_to_max_chars() -> None:
    long_qnames = [f"sym{i}" for i in range(500)]
    p = build_prompt(
        file_path="src/big.py",
        language="python",
        qualified_names=long_qnames,
        inbound_targets=[],
        outbound_targets=[],
        max_chars=300,
    )
    assert len(p) <= 300
    assert "[truncated]" in p


def test_clean_response_strips_fences_and_summary_prefix() -> None:
    raw = "```\nSummary: Module foo handles bar.\n```"
    assert _clean_response(raw) == "Module foo handles bar."


def test_clean_response_handles_plain_text() -> None:
    raw = "  This module wires the X.\n  "
    assert _clean_response(raw) == "This module wires the X."


def test_call_llm_narrative_returns_none_on_http_error(monkeypatch) -> None:
    """Network failure → None (caller falls back to heuristic)."""
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    result = call_llm_narrative(settings, prompt="hi", timeout_sec=0.5)
    assert result is None


def test_call_llm_narrative_parses_canned_response() -> None:
    """Mock httpx and assert the cleaned content comes back."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    class _MockResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"message": {"content": "Foo handles bar via baz."}}

    class _MockClient:
        def __enter__(self):
            return self

        def __exit__(self, *args, **kwargs):
            return None

        def post(self, *args, **kwargs):
            return _MockResponse()

    with patch(
        "agent_memory_lite.extraction.file_narrative_llm.httpx.Client",
        return_value=_MockClient(),
    ):
        result = call_llm_narrative(settings, prompt="hi", timeout_sec=1.0)
    assert result == "Foo handles bar via baz."


def test_call_llm_narrative_returns_none_on_empty_content() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    class _MockResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"message": {"content": "   "}}

    class _MockClient:
        def __enter__(self):
            return self

        def __exit__(self, *args, **kwargs):
            return None

        def post(self, *args, **kwargs):
            return _MockResponse()

    with patch(
        "agent_memory_lite.extraction.file_narrative_llm.httpx.Client",
        return_value=_MockClient(),
    ):
        result = call_llm_narrative(settings, prompt="hi", timeout_sec=1.0)
    assert result is None
