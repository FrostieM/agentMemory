"""v3.1 Vector 3 LLM augmentation (blindspot description)."""

from __future__ import annotations

from typing import Any

import pytest

from agent_memory_lite.maintenance.blindspot_llm import (
    _build_prompt,
    is_llm_enabled,
    llm_describe_blindspot,
    timeout_sec,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_BLINDSPOT_LLM_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_BLINDSPOT_LLM_TIMEOUT_SEC", raising=False)


def test_llm_enabled_by_default() -> None:
    """Updated 2026-05-20: default flipped to True (same rationale
    as Vector 1 LLM gate)."""
    assert is_llm_enabled() is True


def test_llm_env_flag_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_BLINDSPOT_LLM_ENABLED", "false")
    assert is_llm_enabled() is False


def test_timeout_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    assert timeout_sec() == 15.0
    monkeypatch.setenv("MEMORY_BLINDSPOT_LLM_TIMEOUT_SEC", "5")
    assert timeout_sec() == 5.0


def test_prompt_includes_token_and_excerpts() -> None:
    prompt = _build_prompt("rate-limit", 7, ["one", "two", "three"])
    assert "rate-limit" in prompt
    assert "7" in prompt
    assert "- one" in prompt
    assert "- two" in prompt


def test_returns_none_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Operator opt-out: explicit ``=false`` blocks the Ollama call."""
    monkeypatch.setenv("MEMORY_BLINDSPOT_LLM_ENABLED", "false")
    out = llm_describe_blindspot(
        token="x",
        episode_count=5,
        sample_excerpts=["a"],
        base_url="http://127.0.0.1:11434",
        model="qwen2.5:7b-instruct",
    )
    assert out is None


def test_returns_none_when_base_url_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_BLINDSPOT_LLM_ENABLED", "true")
    out = llm_describe_blindspot(
        token="x",
        episode_count=5,
        sample_excerpts=["a"],
        base_url="",
        model="model",
    )
    assert out is None


def test_returns_description_on_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMORY_BLINDSPOT_LLM_ENABLED", "true")

    class _R:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "response": "Rate limiting decisions are scattered across episodes but no decision sets the policy."
            }

    import httpx  # noqa: PLC0415

    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _R())
    out = llm_describe_blindspot(
        token="rate-limit",
        episode_count=7,
        sample_excerpts=["e1"],
        base_url="http://127.0.0.1:11434",
        model="qwen2.5:7b-instruct",
    )
    assert out is not None
    assert "Rate limiting decisions" in out


def test_returns_none_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_BLINDSPOT_LLM_ENABLED", "true")

    import httpx  # noqa: PLC0415

    def _raise(*_a: Any, **_kw: Any) -> None:
        raise httpx.ConnectError("no Ollama")

    monkeypatch.setattr(httpx, "post", _raise)
    out = llm_describe_blindspot(
        token="x",
        episode_count=5,
        sample_excerpts=["a"],
        base_url="http://127.0.0.1:11434",
        model="qwen2.5:7b-instruct",
    )
    assert out is None


def test_response_clamped_to_280_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_BLINDSPOT_LLM_ENABLED", "true")

    long_text = "word " * 100  # 500 chars

    class _R:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"response": long_text}

    import httpx  # noqa: PLC0415

    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _R())
    out = llm_describe_blindspot(
        token="x",
        episode_count=5,
        sample_excerpts=["a"],
        base_url="http://127.0.0.1:11434",
        model="qwen2.5:7b-instruct",
    )
    assert out is not None
    assert len(out) <= 280
    assert out.endswith("...")
