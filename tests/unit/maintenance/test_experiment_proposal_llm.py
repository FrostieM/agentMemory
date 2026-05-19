"""v3.1 Vector 1 LLM body generation (Ollama integration)."""

from __future__ import annotations

from typing import Any

import pytest

from agent_memory_lite.maintenance.experiment_proposal_llm import (
    _build_prompt,
    _split_two_paragraphs,
    is_llm_enabled,
    llm_body_for_insight,
    timeout_sec,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_EXPERIMENT_PROPOSAL_LLM_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_EXPERIMENT_PROPOSAL_LLM_TIMEOUT_SEC", raising=False)


def test_llm_enabled_by_default() -> None:
    """Updated 2026-05-20: default flipped to True after live audit
    proved the heuristic body is noisy. Ollama is part of the
    mandatory stack (README); llm_body_for_insight is failure-soft."""
    assert is_llm_enabled() is True


def test_llm_env_flag_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Operator can opt out per workspace."""
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_LLM_ENABLED", "false")
    assert is_llm_enabled() is False


def test_timeout_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    assert timeout_sec() == 20.0
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_LLM_TIMEOUT_SEC", "5")
    assert timeout_sec() == 5.0


def test_build_prompt_includes_inputs() -> None:
    prompt = _build_prompt("ins_kelly", "Quarter-Kelly bet sizing", 0.55)
    assert "ins_kelly" in prompt
    assert "Quarter-Kelly" in prompt
    assert "0.55" in prompt
    assert "HYPOTHESIS" in prompt
    assert "VALIDATION" in prompt


def test_split_two_paragraphs_happy_path() -> None:
    raw = "Hypothesis paragraph here.\n\nValidation criterion paragraph here."
    out = _split_two_paragraphs(raw)
    assert out is not None
    assert out[0].startswith("Hypothesis paragraph")
    assert out[1].startswith("Validation criterion")


def test_split_two_paragraphs_rejects_single_paragraph() -> None:
    """Model returning only one block → fall back path (None)."""
    out = _split_two_paragraphs("only one paragraph")
    assert out is None


def test_split_two_paragraphs_tolerates_extra_whitespace() -> None:
    raw = "\n\n  Hypothesis here.  \n\n\n\n  Validation here.  \n\n"
    out = _split_two_paragraphs(raw)
    assert out is not None
    assert "Hypothesis here" in out[0]
    assert "Validation here" in out[1]


def test_llm_body_returns_none_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit ``MEMORY_EXPERIMENT_PROPOSAL_LLM_ENABLED=false`` opts
    the workspace out → no Ollama call attempted.

    Updated 2026-05-20: default flipped to True, so the disabled-path
    test sets the flag false explicitly."""
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_LLM_ENABLED", "false")
    out = llm_body_for_insight(
        insight_id="ins_x",
        summary="topic",
        confidence=0.55,
        base_url="http://127.0.0.1:11434",
        model="qwen2.5:7b-instruct",
    )
    assert out is None


def test_llm_body_returns_none_when_base_url_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_LLM_ENABLED", "true")
    out = llm_body_for_insight(
        insight_id="ins_x",
        summary="topic",
        confidence=0.55,
        base_url="",  # empty — short-circuit
        model="model",
    )
    assert out is None


def test_llm_body_calls_ollama_and_parses_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When enabled + Ollama reachable, returns the two paragraphs."""
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_LLM_ENABLED", "true")

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "response": (
                    "Hypothesis: quarter-Kelly improves drawdown on volatile pairs.\n\n"
                    "Validation: surface 2+ trade episodes with drawdown reduction."
                )
            }

    def _fake_post(*_args: Any, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse()

    import httpx  # noqa: PLC0415

    monkeypatch.setattr(httpx, "post", _fake_post)
    out = llm_body_for_insight(
        insight_id="ins_kelly",
        summary="Quarter-Kelly may improve drawdown",
        confidence=0.55,
        base_url="http://127.0.0.1:11434",
        model="qwen2.5:7b-instruct",
    )
    assert out is not None
    assert "quarter-Kelly improves drawdown" in out[0]
    assert "2+ trade episodes" in out[1]


def test_llm_body_returns_none_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama 5xx → caller gets None and falls back to heuristic."""
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_LLM_ENABLED", "true")

    import httpx  # noqa: PLC0415

    def _raise_http(*_args: Any, **_kwargs: Any) -> Any:
        raise httpx.ConnectError("no Ollama")

    monkeypatch.setattr(httpx, "post", _raise_http)
    out = llm_body_for_insight(
        insight_id="ins_x",
        summary="topic",
        confidence=0.55,
        base_url="http://127.0.0.1:11434",
        model="qwen2.5:7b-instruct",
    )
    assert out is None


def test_llm_body_returns_none_on_empty_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ollama returning empty body → fall back to heuristic."""
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_LLM_ENABLED", "true")

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"response": "   "}

    def _fake_post(*_args: Any, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse()

    import httpx  # noqa: PLC0415

    monkeypatch.setattr(httpx, "post", _fake_post)
    out = llm_body_for_insight(
        insight_id="ins_x",
        summary="topic",
        confidence=0.55,
        base_url="http://127.0.0.1:11434",
        model="qwen2.5:7b-instruct",
    )
    assert out is None


def test_llm_body_returns_none_on_single_paragraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model returning a single paragraph (didn't follow instructions) →
    caller gets None and falls back to heuristic."""
    monkeypatch.setenv("MEMORY_EXPERIMENT_PROPOSAL_LLM_ENABLED", "true")

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"response": "just one paragraph no validation"}

    def _fake_post(*_args: Any, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse()

    import httpx  # noqa: PLC0415

    monkeypatch.setattr(httpx, "post", _fake_post)
    out = llm_body_for_insight(
        insight_id="ins_x",
        summary="topic",
        confidence=0.55,
        base_url="http://127.0.0.1:11434",
        model="qwen2.5:7b-instruct",
    )
    assert out is None
