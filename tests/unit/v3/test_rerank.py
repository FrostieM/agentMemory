"""Unit tests for v3 cross-encoder reranker.

The reranker is OPT-IN — sentence-transformers is not a required dep.
These tests focus on the failure-soft contract (works without the
optional dep, never raises) and verify that *when* the model loads,
it actually reorders hits by score.

When sentence-transformers IS installed, we stub the CrossEncoder
class so the test stays fast (no model download).
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from typing import Any

import pytest

from agent_memory_lite.v3.retrieval import rerank


@dataclass(frozen=True, slots=True)
class _FakeHit:
    """Mirror SearchHit shape just enough for the reranker."""

    kind: str
    projection: dict[str, Any]
    score: float


# ============================================================
# is_available / failure-soft behaviour
# ============================================================


def test_rerank_empty_returns_empty() -> None:
    assert rerank.rerank_hits("query", []) == []


def test_rerank_falls_back_when_model_load_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _load_model returns None (extra not installed / load failure),
    rerank_hits must return the first top_k of the input unchanged."""
    hits = [
        _FakeHit(kind="decision", projection={"title": f"d{i}"}, score=1.0 - i * 0.1)
        for i in range(8)
    ]
    monkeypatch.setattr(rerank, "_load_model", lambda _name: None)
    out = rerank.rerank_hits("query", hits, top_k=3)
    assert len(out) == 3
    # Order preserved because no reranking happened.
    assert [h.projection["title"] for h in out] == ["d0", "d1", "d2"]


def test_rerank_failure_during_predict_returns_original_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BoomModel:
        def predict(self, _pairs: list[tuple[str, str]]) -> list[float]:
            raise RuntimeError("model died mid-predict")

    monkeypatch.setattr(rerank, "_load_model", lambda _name: _BoomModel())
    hits = [_FakeHit(kind="decision", projection={"title": f"d{i}"}, score=0.5) for i in range(3)]
    out = rerank.rerank_hits("q", hits, top_k=2)
    assert [h.projection["title"] for h in out] == ["d0", "d1"]


# ============================================================
# Snippet builder (per-kind)
# ============================================================


def test_snippet_for_decision_uses_title_and_gist() -> None:
    hit = _FakeHit(
        kind="decision",
        projection={"title": "Use Kelly", "gist": "Quarter-Kelly sizing only."},
        score=0.5,
    )
    snippet = rerank._snippet_for(hit)
    assert "Use Kelly" in snippet
    assert "Quarter-Kelly" in snippet


def test_snippet_for_behavior_uses_name_and_rule_one_line() -> None:
    hit = _FakeHit(
        kind="behavior",
        projection={"name": "evidence-first", "rule_one_line": "Cite source/confidence."},
        score=0.5,
    )
    snippet = rerank._snippet_for(hit)
    assert "evidence-first" in snippet
    assert "Cite source/confidence" in snippet


def test_snippet_for_code_digest_uses_file_path_and_purpose() -> None:
    hit = _FakeHit(
        kind="code_digest",
        projection={"file_path": "src/foo.py", "purpose_short": "Compute fees."},
        score=0.5,
    )
    snippet = rerank._snippet_for(hit)
    assert "src/foo.py" in snippet
    # Trailing "." may be stripped by the snippet builder's normalization.
    assert "Compute fees" in snippet


def test_snippet_for_unknown_kind_falls_back_to_repr() -> None:
    hit = _FakeHit(kind="unknown", projection={"weird": "stuff"}, score=0.5)
    snippet = rerank._snippet_for(hit)
    assert "weird" in snippet


# ============================================================
# Successful reranking with a stubbed CrossEncoder
# ============================================================


class _StubCrossEncoder:
    """Lightweight stand-in for sentence_transformers.CrossEncoder.

    Scores each pair as ``len(intersection of query+doc tokens)``,
    so the reranker output is deterministic and easy to reason about.
    """

    def __init__(self, _model_name: str) -> None:
        pass

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        scores: list[float] = []
        for query, doc in pairs:
            qset = {t.lower() for t in query.split()}
            dset = {t.lower() for t in doc.split()}
            scores.append(float(len(qset & dset)))
        return scores


@pytest.fixture
def stub_sentence_transformers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a fake ``sentence_transformers`` module exposing CrossEncoder."""
    fake_module = types.ModuleType("sentence_transformers")
    fake_module.CrossEncoder = _StubCrossEncoder  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    # Reset the rerank module's model cache so the stub is freshly loaded.
    monkeypatch.setattr(rerank, "_model_cache", {})


def test_rerank_reorders_by_score(stub_sentence_transformers: None) -> None:
    hits = [
        _FakeHit(kind="decision", projection={"title": "alpha beta"}, score=0.5),
        _FakeHit(kind="decision", projection={"title": "kelly sizing review"}, score=0.5),
        _FakeHit(kind="decision", projection={"title": "gamma delta"}, score=0.5),
    ]
    out = rerank.rerank_hits("kelly sizing", hits, top_k=3)
    # Top hit should be the one whose title overlaps the query.
    assert out[0].projection["title"] == "kelly sizing review"


def test_rerank_respects_top_k(stub_sentence_transformers: None) -> None:
    hits = [_FakeHit(kind="decision", projection={"title": f"t{i}"}, score=0.5) for i in range(8)]
    out = rerank.rerank_hits("query", hits, top_k=3)
    assert len(out) == 3


def test_is_available_reflects_stubbed_module(stub_sentence_transformers: None) -> None:
    """With the stub installed, the optional-dep probe succeeds."""
    assert rerank.is_available() is True


def test_is_available_returns_false_without_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the optional dep is genuinely missing, is_available() reports False."""
    # If sentence_transformers is currently importable, drop it for this test.
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # type: ignore[arg-type]
    assert rerank.is_available() is False
