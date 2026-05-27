"""Regression: ``models.enums.coerce_enum`` is the universal safety net.

A bug pattern hit production twice in v3.4 before being caught:

1. ``autonomous_loop`` wrote ``theory_evidence.kind='autonomous_corroboration'``
   via raw SQL before that value was in ``TheoryEvidenceKind``.
2. The code indexer wrote ``chunks.kind='block'`` / ``'symbol'`` via raw
   SQL before those values were in ``ChunkKind``.

Both crashed every compact read call with HTTP 500 — the
ValueError raised by ``EnumCls(row["col"])`` inside a row→model parser
escaped the whole envelope route.

The fix is two-layered: register the canonical values as first-class
enum members, AND wrap every parser with ``coerce_enum`` so future
unknown labels degrade to a safe fallback instead of crashing the
read path. This test locks the contract.
"""

from __future__ import annotations

import pytest

from agent_memory_lite.models.enums import (
    ChunkKind,
    EpisodeSource,
    InsightStatus,
    InsightType,
    TheoryEvidenceKind,
    TrustLevel,
    coerce_enum,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("supporting", TheoryEvidenceKind.SUPPORTING),
        ("autonomous_corroboration", TheoryEvidenceKind.AUTONOMOUS_CORROBORATION),
        (TheoryEvidenceKind.REFUTING, TheoryEvidenceKind.REFUTING),
    ],
)
def test_coerce_passes_through_known_values(raw: object, expected: TheoryEvidenceKind) -> None:
    """Known string values + enum instances must round-trip without
    going through the fallback — the fallback is for unknowns only."""
    assert coerce_enum(TheoryEvidenceKind, raw, TheoryEvidenceKind.NEUTRAL) is expected


def test_coerce_unknown_string_returns_fallback() -> None:
    """The ValueError must not escape — that was the bug."""
    assert (
        coerce_enum(TheoryEvidenceKind, "future_label", TheoryEvidenceKind.MIXED)
        is TheoryEvidenceKind.MIXED
    )


def test_coerce_none_returns_fallback() -> None:
    """A NULL DB column shouldn't crash the parser."""
    assert coerce_enum(ChunkKind, None, ChunkKind.DOC) is ChunkKind.DOC


def test_coerce_non_string_returns_fallback() -> None:
    """Garbage data type (int, bytes, list) — still no exception."""
    assert coerce_enum(ChunkKind, 42, ChunkKind.DOC) is ChunkKind.DOC
    assert coerce_enum(ChunkKind, [1, 2], ChunkKind.DOC) is ChunkKind.DOC


def test_v3_4_drift_values_are_registered() -> None:
    """The five drift values found in live DB at v3.4 must be
    first-class enum members so they round-trip with full semantics.
    The coerce fallback exists for FUTURE drift, not for these — a
    silent downgrade to a generic fallback here would lose meaning
    (e.g. autonomous_corroboration → SUPPORTING would mix
    agent-derived evidence with human-derived in the UI)."""
    assert TheoryEvidenceKind("autonomous_corroboration") is (
        TheoryEvidenceKind.AUTONOMOUS_CORROBORATION
    )
    assert ChunkKind("block") is ChunkKind.BLOCK
    assert ChunkKind("symbol") is ChunkKind.SYMBOL
    assert EpisodeSource("manual") is EpisodeSource.MANUAL
    assert EpisodeSource("user_provided") is EpisodeSource.USER_PROVIDED
    assert TrustLevel("user_provided") is TrustLevel.USER_PROVIDED
    assert InsightType("consolidation") is InsightType.CONSOLIDATION
    assert InsightStatus("candidate") is InsightStatus.CANDIDATE


def test_coerce_with_every_enum_class() -> None:
    """Catch a regression where someone overloads ``coerce_enum`` to
    only handle specific enums — it must stay generic."""
    enums_to_check = [
        (TheoryEvidenceKind, TheoryEvidenceKind.NEUTRAL),
        (ChunkKind, ChunkKind.DOC),
        (EpisodeSource, EpisodeSource.SYSTEM),
        (TrustLevel, TrustLevel.UNKNOWN),
        (InsightType, InsightType.LESSON),
        (InsightStatus, InsightStatus.NEW),
    ]
    for enum_cls, fallback in enums_to_check:
        # Round-trip a known value:
        first = next(iter(enum_cls))
        assert coerce_enum(enum_cls, first.value, fallback) is first
        # Fallback for an unknown string:
        assert coerce_enum(enum_cls, "definitely_not_a_member_xyz", fallback) is fallback
