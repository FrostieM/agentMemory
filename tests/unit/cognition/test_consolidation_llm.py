"""v3.2 — LLM-augmented consolidation summary tests.

Mirrors the experiment_proposal_llm test shape: pin the failure-soft
contract by mocking httpx, then exercise the prompt + parse paths.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from agent_memory_lite.cognition import consolidation_llm as cl


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_CONSOLIDATION_LLM_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_CONSOLIDATION_LLM_TIMEOUT_SEC", raising=False)


def test_default_enabled_v3_2() -> None:
    """Live-2026-05-20 audit: word-frequency summaries are noise →
    LLM default flipped to ON."""
    assert cl.is_llm_enabled() is True


def test_disabled_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_CONSOLIDATION_LLM_ENABLED", "false")
    out = cl.llm_distill_cluster(
        excerpts=["episode body one", "episode body two"],
        signal_tokens=["token"],
        member_count=2,
        base_url="http://127.0.0.1:11434",
        model="qwen2.5:7b-instruct",
    )
    assert out is None


def test_empty_excerpts_returns_none() -> None:
    """No episode text to read → nothing the LLM can summarize."""
    out = cl.llm_distill_cluster(
        excerpts=[],
        signal_tokens=["a"],
        member_count=2,
        base_url="http://127.0.0.1:11434",
        model="qwen2.5:7b-instruct",
    )
    assert out is None


def test_missing_base_url_or_model_returns_none() -> None:
    assert (
        cl.llm_distill_cluster(
            excerpts=["x"], signal_tokens=[], member_count=1, base_url="", model="m"
        )
        is None
    )
    assert (
        cl.llm_distill_cluster(
            excerpts=["x"], signal_tokens=[], member_count=1, base_url="u", model=""
        )
        is None
    )


def test_no_pattern_sentinel_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Ollama can't find a meaningful pattern it returns the
    NO_PATTERN sentinel — we must drop it so the heuristic fires."""

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:  # pragma: no cover - trivial
            return None

        def json(self) -> dict[str, Any]:
            return {"response": "NO_PATTERN"}

    monkeypatch.setattr(cl, "is_llm_enabled", lambda: True)
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _Resp())
    out = cl.llm_distill_cluster(
        excerpts=["a", "b"],
        signal_tokens=["docs"],
        member_count=3,
        base_url="http://127.0.0.1:11434",
        model="qwen2.5:7b-instruct",
    )
    assert out is None


def test_good_response_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: Ollama returns a sensible 1-line summary."""

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:  # pragma: no cover
            return None

        def json(self) -> dict[str, Any]:
            return {
                "response": (
                    "Pattern: pre-commit hook keeps ingesting file_indexed events "
                    "for src/ tree changes."
                )
            }

    monkeypatch.setattr(cl, "is_llm_enabled", lambda: True)
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _Resp())
    out = cl.llm_distill_cluster(
        excerpts=["file_indexed: src/a.py", "file_indexed: src/b.py"],
        signal_tokens=["file_indexed", "src"],
        member_count=5,
        base_url="http://127.0.0.1:11434",
        model="qwen2.5:7b-instruct",
    )
    assert out is not None
    assert out.startswith("Pattern:")
    assert "file_indexed" in out


def test_response_stripped_of_quotes_and_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama sometimes wraps its reply in surrounding quotes — strip."""

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:  # pragma: no cover
            return None

        def json(self) -> dict[str, Any]:
            return {"response": '  "Pattern: documentation touched in five episodes."  '}

    monkeypatch.setattr(cl, "is_llm_enabled", lambda: True)
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _Resp())
    out = cl.llm_distill_cluster(
        excerpts=["x"], signal_tokens=["docs"], member_count=5, base_url="u", model="m"
    )
    assert out == "Pattern: documentation touched in five episodes."


def test_long_response_truncated_at_160(monkeypatch: pytest.MonkeyPatch) -> None:
    long_text = "Pattern: " + ("x" * 300)

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:  # pragma: no cover
            return None

        def json(self) -> dict[str, Any]:
            return {"response": long_text}

    monkeypatch.setattr(cl, "is_llm_enabled", lambda: True)
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _Resp())
    out = cl.llm_distill_cluster(
        excerpts=["x"], signal_tokens=[], member_count=2, base_url="u", model="m"
    )
    assert out is not None
    assert len(out) <= 160


def test_only_first_line_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the model adds a stray follow-up line, we only keep the first."""

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:  # pragma: no cover
            return None

        def json(self) -> dict[str, Any]:
            return {"response": "Pattern: kelly sizing bet\nExtra explanation line."}

    monkeypatch.setattr(cl, "is_llm_enabled", lambda: True)
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _Resp())
    out = cl.llm_distill_cluster(
        excerpts=["x"], signal_tokens=["kelly"], member_count=3, base_url="u", model="m"
    )
    assert out == "Pattern: kelly sizing bet"


def test_http_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama unreachable → None → heuristic fallback."""
    monkeypatch.setattr(cl, "is_llm_enabled", lambda: True)

    def boom(*_a: Any, **_kw: Any) -> Any:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", boom)
    out = cl.llm_distill_cluster(
        excerpts=["x"], signal_tokens=[], member_count=2, base_url="u", model="m"
    )
    assert out is None


def test_empty_response_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:  # pragma: no cover
            return None

        def json(self) -> dict[str, Any]:
            return {"response": ""}

    monkeypatch.setattr(cl, "is_llm_enabled", lambda: True)
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _Resp())
    out = cl.llm_distill_cluster(
        excerpts=["x"], signal_tokens=[], member_count=2, base_url="u", model="m"
    )
    assert out is None


def test_excerpts_capped_to_five() -> None:
    """The prompt should never include more than 5 excerpts to keep the
    Ollama context window predictable."""
    prompt = cl._build_prompt(
        excerpts=[f"episode body {i}" for i in range(12)],
        signal_tokens=["token"],
        member_count=12,
    )
    # Count numbered list entries (lines starting with "1. " .. "5. ").
    list_marks = sum(1 for n in range(1, 13) if f"{n}. " in prompt)
    assert list_marks == 5
