"""Unit tests for v1.10 correction-pattern detector.

Mix of table-driven cases for documented examples and hypothesis
property tests that ensure invariants hold across generated input.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agent_memory_lite.extraction.correction_patterns import (
    CORRECTION_BODY,
    CORRECTION_OPENERS,
    LIKELY_AGREEMENT,
    match_correction,
)

# ---------- table-driven positive cases (the "real corrections" corpus) ------


REAL_CORRECTIONS = [
    # Today's three corrections, in their original Russian shape.
    pytest.param(
        "нет, MCP не мог стартовать до 1.1.0, я только что его запустил после твоего вопроса",
        0.85,
        id="mcp-started-after-1-1-0",
    ),
    pytest.param(
        "он не мог стартовать до релиза, audit-записи это доказывают",
        0.50,
        id="body-only-russian",
    ),
    pytest.param(
        "wait, you confused 422 with 404 — that doesnt mean what you think",
        0.85,
        id="wait-with-body-english",
    ),
    pytest.param(
        "no, the schema mismatch is in the request payload, rationale vs reason",
        0.70,
        id="no-without-body",
    ),
    pytest.param(
        "Actually, that doesn't quite work because the timestamps are after the release date",
        0.85,
        id="actually-with-doesnt",
    ),
]


@pytest.mark.parametrize("text,expected_conf", REAL_CORRECTIONS)
def test_real_corrections_match(text: str, expected_conf: float) -> None:
    m = match_correction(text)
    assert m.matched
    assert m.confidence == pytest.approx(expected_conf)


def test_em_dash_after_no_matches_opener() -> None:
    """Russian idiom "нет — это не так" should match the opener pattern."""
    m = match_correction("нет — это не так, проверь даты в audit_log еще раз")
    assert m.matched
    assert m.opener_match is not None
    assert m.confidence >= 0.7


def test_en_dash_after_no_matches_opener() -> None:
    m = match_correction("нет – это не так, поправляю выводы по данным")
    assert m.matched
    assert m.opener_match is not None


# ---------- negative cases ---------------------------------------------------


NON_CORRECTIONS = [
    pytest.param("спасибо за помощь, продолжай в том же духе", id="thanks"),
    pytest.param("ок, давай пробуем", id="ok-lets-try"),
    pytest.param("please run the carousel one more time", id="request-rerun"),
    pytest.param("что делаем дальше?", id="open-question"),
    pytest.param("что-бы тестов было больше, добавь hypothesis", id="feature-request"),
]


@pytest.mark.parametrize("text", NON_CORRECTIONS)
def test_non_corrections_dont_match(text: str) -> None:
    m = match_correction(text)
    assert not m.matched
    assert m.confidence == 0.0


# ---------- agreement filter ------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "нет проблем",
        "нет проблем.",
        "no problem",
        "no thanks",
        "Nope.",
        "нет, спасибо",
    ],
)
def test_short_agreement_phrases_rejected(text: str) -> None:
    # min_user_len lowered so the agreement filter is what gates these
    m = match_correction(text, min_user_len=5)
    assert not m.matched
    assert m.rejected_as_agreement


def test_agreement_followed_by_directive_does_not_filter() -> None:
    # "нет, делай как считаешь нужным" passes length filter and matches
    # opener; it's borderline — operator review handles the remaining
    # noise. Confidence should be opener-only.
    m = match_correction("нет, делай как считаешь нужным дальше", min_user_len=5)
    # opener matched; not filtered as agreement (too long for agreement regex)
    assert not m.rejected_as_agreement
    assert m.opener_match is not None


# ---------- length thresholds ------------------------------------------------


def test_short_user_message_rejected_before_pattern_check() -> None:
    m = match_correction("нет.", min_user_len=30)
    assert not m.matched
    assert m.confidence == 0.0
    assert m.opener_match is None  # short-circuit before regex


def test_min_confidence_filter_blocks_below_threshold() -> None:
    # body-only -> conf=0.50; raise threshold above to filter it
    m = match_correction(
        "он не мог стартовать до релиза, я проверил audit",
        min_user_len=20,
        min_confidence=0.6,
    )
    assert m.confidence == 0.50
    assert not m.matched


# ---------- hypothesis property tests ---------------------------------------


@settings(max_examples=200, deadline=None)
@given(text=st.text(min_size=0, max_size=4000))
def test_confidence_in_unit_range(text: str) -> None:
    m = match_correction(text)
    assert 0.0 <= m.confidence <= 1.0


@settings(max_examples=200, deadline=None)
@given(text=st.text(min_size=0, max_size=4000))
def test_matched_implies_confidence_above_threshold(text: str) -> None:
    m = match_correction(text, min_confidence=0.5)
    if m.matched:
        assert m.confidence >= 0.5
    else:
        assert m.confidence == 0.0 or m.confidence < 0.5


@settings(max_examples=200, deadline=None)
@given(
    pad=st.text(alphabet=st.sampled_from(" \t\n"), min_size=0, max_size=8),
    body=st.text(min_size=0, max_size=400),
)
def test_idempotent_on_outer_whitespace(pad: str, body: str) -> None:
    """match_correction is invariant to outer whitespace padding."""
    a = match_correction(body, min_user_len=10)
    b = match_correction(pad + body + pad, min_user_len=10)
    # confidence and matched outcome unchanged when only whitespace differs
    # around the message body
    assert a.confidence == b.confidence
    assert a.matched == b.matched


# ---------- regex shape sanity ---------------------------------------------


def test_openers_pattern_has_multiline_flag() -> None:
    assert CORRECTION_OPENERS.flags & re.MULTILINE
    assert CORRECTION_OPENERS.flags & re.IGNORECASE


def test_body_pattern_has_multiline_flag() -> None:
    assert CORRECTION_BODY.flags & re.MULTILINE
    assert CORRECTION_BODY.flags & re.IGNORECASE


def test_agreement_pattern_anchored_to_whole_message() -> None:
    # The agreement filter is intentionally tight: only matches when the
    # WHOLE short message is an agreement phrase, not when "нет проблем"
    # appears inside a longer correction.
    text = "нет, проблема в том что timestamps сдвинулись на день"
    assert LIKELY_AGREEMENT.search(text) is None
