"""Search + range helpers used by decisions_repo.

Pulled out of ``decisions_repo.py`` so that file stays under the
SLOC ceiling once pinned-first ordering and since/until date filters
land on ``list_active_decisions`` / ``list_all_decisions``.
"""

from __future__ import annotations

import re

from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.utils.sql_filters import date_range_clause

__all__ = ["date_range_clause", "filter_rank_limit"]

_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)


def _tokens(query: str | None) -> list[str]:
    if not query:
        return []
    return [token.lower() for token in _TOKEN_RE.findall(query) if len(token) > 1]


def _title_text(decision: Decision) -> str:
    return decision.title.lower()


def _body_text(decision: Decision) -> str:
    return " ".join([decision.decision_text, decision.rationale or ""]).lower()


# Ranker weights — tuned 2026-05-20 against MemBench MVP on the
# agent-memory-lite workspace (45 decisions). Baseline before the
# tweak was MRR=0.3611, recall@5=100% (matching row was always in
# top-10 but rarely at rank 1). The dominant failure mode was
# title-verbatim queries colliding with sibling decisions whose
# titles share a single high-signal noun (e.g. "MemBench MVP..."
# losing to "MemBench v2..." because both score 1.0 per shared
# token under the old equal-weight ranker).
#
# Knobs:
#   - title tokens score 3x body tokens, because the query in
#     practice IS the title (membench, brief composition, UI
#     dropdown autocomplete all feed title-shaped strings).
#   - all-query-tokens-in-title bonus gives the exact match the
#     decisive lead over partial siblings (5.0 is large enough to
#     dominate importance + confidence noise).
#   - outcome_score additive factor [-0.5, +0.5]: a decision
#     superseded by a better one or marked low-outcome should sink,
#     even when its title still token-matches.
_TITLE_TOKEN_WEIGHT = 3.0
_BODY_TOKEN_WEIGHT = 1.0
_ALL_TOKENS_IN_TITLE_BONUS = 5.0
_OUTCOME_WEIGHT = 0.5
# When a query is present, pinned becomes a content-score BOOST instead
# of an absolute sort-key. The boost is smaller than the all-tokens-in-
# title bonus on purpose: a precise content match should still beat a
# tangentially-related pinned row. Tuned so two pinned decisions cannot
# block a non-pinned exact-title-match from rank 1 — that was the v3.4
# MemBench failure mode (MRR capped at ~0.36 because pinned always sat
# above the matching row when query targeted a non-pinned decision).
_PINNED_QUERY_BOOST = 2.0


def _rank(decision: Decision, tokens: list[str]) -> tuple[int, float, str]:
    """Rank tuple: ``(pinned_lead, score, updated_at)`` sorted DESC.

    ``pinned_lead`` is set to 1 ONLY when no query tokens are supplied —
    the list-decisions endpoint is then in "browse" mode where the
    operator expects pinned rows at the top. Once tokens are present
    the caller is searching, and ``pinned_lead`` collapses to 0 so the
    content score (title-weighted token match + exact-title bonus +
    outcome + importance) decides the order. Pinned status still bumps
    the score via ``_PINNED_QUERY_BOOST`` so two equally-matched rows
    break in favour of the pinned one.
    """
    title = _title_text(decision)
    body = _body_text(decision)
    title_matches = sum(1.0 for token in tokens if token in title)
    body_matches = sum(1.0 for token in tokens if token in body)
    token_score = title_matches * _TITLE_TOKEN_WEIGHT + body_matches * _BODY_TOKEN_WEIGHT
    # Exact-title-match bonus: when ALL query tokens land in the title
    # the caller is almost certainly addressing this specific decision
    # (membench drives the query off the title verbatim; brief shows
    # decisions by title; the UI search box is mostly fed title text).
    if tokens and title_matches == len(tokens):
        token_score += _ALL_TOKENS_IN_TITLE_BONUS
    outcome = getattr(decision, "outcome_score", 0.0) or 0.0
    score = (
        token_score
        + decision.importance
        + (decision.confidence * 0.25)
        + (float(outcome) * _OUTCOME_WEIGHT)
    )
    if tokens and decision.pinned:
        score += _PINNED_QUERY_BOOST
    pinned_lead = 1 if (decision.pinned and not tokens) else 0
    return (pinned_lead, score, decision.updated_at)


def filter_rank_limit(
    decisions: list[Decision],
    *,
    query: str | None,
    limit: int | None,
) -> list[Decision]:
    terms = _tokens(query)
    decisions.sort(key=lambda decision: _rank(decision, terms), reverse=True)
    return decisions if limit is None else decisions[:limit]
