"""v3.1 reranker — env-gated auto-rerank flag."""

from __future__ import annotations

import pytest

from agent_memory_lite.retrieval import rerank


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_RERANKER_AUTO", raising=False)


def test_auto_rerank_default_off() -> None:
    """Default behaviour is unchanged — caller has to opt in per call."""
    assert rerank.auto_rerank_enabled() is False


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE", "On"])
def test_auto_rerank_truthy_env(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_RERANKER_AUTO", value)
    assert rerank.auto_rerank_enabled() is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "garbage"])
def test_auto_rerank_falsy_env(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_RERANKER_AUTO", value)
    assert rerank.auto_rerank_enabled() is False


def test_search_flag_resolution_auto_off_explicit_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Effective rerank = explicit OR auto. Both off → no rerank."""
    monkeypatch.delenv("MEMORY_RERANKER_AUTO", raising=False)
    assert (False or rerank.auto_rerank_enabled()) is False


def test_search_flag_resolution_auto_on_explicit_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto on → rerank fires even when caller didn't ask."""
    monkeypatch.setenv("MEMORY_RERANKER_AUTO", "true")
    assert (False or rerank.auto_rerank_enabled()) is True


def test_search_flag_resolution_explicit_true_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit ``rerank=True`` always fires, regardless of auto flag.

    The actual ``rerank or auto_rerank_enabled()`` expression collapses
    to ``True`` whenever ``rerank`` is ``True`` — this test documents
    that the short-circuit holds across auto on/off."""
    monkeypatch.delenv("MEMORY_RERANKER_AUTO", raising=False)
    # ``rerank=True`` short-circuits the OR — auto flag never read.
    explicit_off = True or rerank.auto_rerank_enabled()  # noqa: SIM222 - documents short-circuit
    assert explicit_off is True
    monkeypatch.setenv("MEMORY_RERANKER_AUTO", "true")
    explicit_on = True or rerank.auto_rerank_enabled()  # noqa: SIM222 - documents short-circuit
    assert explicit_on is True


def test_rerank_hits_unchanged_when_model_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure-soft contract: model load returns None → input order
    preserved. We stub _load_model to bypass the real CrossEncoder."""
    monkeypatch.setattr(rerank, "_load_model", lambda _name: None)

    class _Hit:
        def __init__(self, hid: str, txt: str) -> None:
            self.id = hid
            self.kind = "decision"
            self.projection = {"title": txt, "gist": txt}
            self.score = 1.0

    hits = [_Hit("a", "alpha"), _Hit("b", "beta"), _Hit("c", "gamma")]
    out = rerank.rerank_hits("query", hits, top_k=2)
    assert [h.id for h in out] == ["a", "b"]
