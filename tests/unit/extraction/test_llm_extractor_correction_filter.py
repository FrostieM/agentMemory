"""Regression tests for the v1.2.4 LLM-extractor CORRECTION filter.

Live regression in copyBot 2026-05-05: the LLM (Ollama) extractor produced
3 ``kind=correction`` candidates from a single ``agent_action`` episode that
contained audit-findings phrasing ("HIGH issue X — fixed"). Those were NOT
user corrections — the v1.10 (claim, correction) pair semantics belong to
``user_message`` episodes only. The fix in ``llm_extractor.py::_parse``
drops CORRECTION-kind items when the source episode's ``source_type`` is
not ``USER_MESSAGE``.

These tests bypass the actual Ollama call (private ``_parse`` only) so they
are hermetic and don't need a running model.
"""

from __future__ import annotations

import json

from agent_memory_lite.extraction.llm_extractor import OllamaExtractor
from agent_memory_lite.models.enums import EpisodeSource, MemoryCandidateKind, TrustLevel
from agent_memory_lite.models.episodes import Episode


def _episode(*, source_type: EpisodeSource) -> Episode:
    return Episode(
        id="ep_x",
        workspace_id="default",
        session_id=None,
        task_id=None,
        source_type=source_type,
        raw_text="placeholder",
        summary=None,
        trust_level=TrustLevel.AGENT_OBSERVED,
        importance=0.7,
        confidence=1.0,
        created_at="2026-05-05T12:00:00Z",
    )


def _llm_response(items: list[dict]) -> str:
    return json.dumps(items)


def _make_extractor() -> OllamaExtractor:
    return OllamaExtractor(base_url="http://localhost:11434", model="dummy")


def test_correction_from_agent_action_episode_is_dropped() -> None:
    """Live copyBot regression: LLM emitted CORRECTION-kind from an
    agent_action episode containing 'HIGH issue X — fixed'. Without the
    1.2.4 filter these polluted the operator review queue. After the
    filter: zero CORRECTION candidates from non-user_message episodes."""
    ext = _make_extractor()
    payload = _llm_response(
        [
            {
                "kind": "correction",
                "subject": "paperNet/paperCash floor",
                "predicate": "fixed",
                "evidence": "audit finding fixed",
                "confidence": 0.9,
                "importance": 0.85,
            }
        ]
    )
    out = ext._parse(payload, _episode(source_type=EpisodeSource.AGENT_ACTION))
    assert out == []


def test_correction_from_user_message_episode_is_kept() -> None:
    """Inverse: when the source is a real user_message, CORRECTION
    survives. v1.10 semantics preserved."""
    ext = _make_extractor()
    payload = _llm_response(
        [
            {
                "kind": "correction",
                "subject": "verify timestamps before release-date claims",
                "predicate": "should_avoid",
                "evidence": "user said the release was after the audit window",
                "confidence": 0.8,
                "importance": 0.75,
            }
        ]
    )
    out = ext._parse(payload, _episode(source_type=EpisodeSource.USER_MESSAGE))
    assert len(out) == 1
    assert out[0].kind == MemoryCandidateKind.CORRECTION
    assert "verify timestamps" in out[0].subject


def test_non_correction_kinds_pass_through_from_agent_action() -> None:
    """The filter is targeted: only CORRECTION kind requires user_message.
    Decisions, project_facts, bugs, fixes — all still pass when the LLM
    emits them from an agent_action episode."""
    ext = _make_extractor()
    payload = _llm_response(
        [
            {
                "kind": "decision",
                "subject": "ship phase 7.A.1",
                "predicate": "decided",
                "evidence": "ok",
                "confidence": 0.9,
                "importance": 0.85,
            },
            {
                "kind": "project_fact",
                "subject": "two bots",
                "predicate": "is",
                "evidence": "copyBot + makerBot",
                "confidence": 1.0,
                "importance": 0.9,
            },
            {
                "kind": "bug",
                "subject": "selected vs enabled",
                "predicate": "fix",
                "evidence": "x",
                "confidence": 0.8,
                "importance": 0.7,
            },
        ]
    )
    out = ext._parse(payload, _episode(source_type=EpisodeSource.AGENT_ACTION))
    kinds = {c.kind for c in out}
    assert MemoryCandidateKind.DECISION in kinds
    assert MemoryCandidateKind.PROJECT_FACT in kinds
    assert MemoryCandidateKind.BUG in kinds
    assert MemoryCandidateKind.CORRECTION not in kinds


def test_mixed_payload_drops_only_correction_when_source_not_user() -> None:
    """LLM may return both correction and other kinds in one response.
    The filter must drop only the correction items, keeping the rest."""
    ext = _make_extractor()
    payload = _llm_response(
        [
            {
                "kind": "correction",
                "subject": "audit finding 1",
                "predicate": "fixed",
                "evidence": "x",
                "confidence": 0.9,
                "importance": 0.8,
            },
            {
                "kind": "decision",
                "subject": "deploy now",
                "predicate": "decided",
                "evidence": "y",
                "confidence": 0.9,
                "importance": 0.8,
            },
            {
                "kind": "correction",
                "subject": "audit finding 2",
                "predicate": "fixed",
                "evidence": "z",
                "confidence": 0.9,
                "importance": 0.8,
            },
        ]
    )
    out = ext._parse(payload, _episode(source_type=EpisodeSource.AGENT_ACTION))
    assert len(out) == 1
    assert out[0].kind == MemoryCandidateKind.DECISION
    assert "deploy now" in out[0].subject
