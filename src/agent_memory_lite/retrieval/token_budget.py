"""Greedy budget-respecting selection.

Walks scored hits in order; picks each one whose `text` plus a small fixed
overhead fits within the remaining budget. Items that would overflow are
skipped, not truncated — phase 2 keeps it simple.
"""

from __future__ import annotations

from agent_memory_lite.models.retrieval import ScoredHit
from agent_memory_lite.utils.tokens import estimate_tokens

PER_HIT_OVERHEAD_TOKENS = 16


def fit_within_budget(hits: list[ScoredHit], *, max_tokens: int) -> list[ScoredHit]:
    if max_tokens <= 0:
        return []
    chosen: list[ScoredHit] = []
    used = 0
    for hit in hits:
        cost = estimate_tokens(hit.text) + PER_HIT_OVERHEAD_TOKENS
        if used + cost > max_tokens:
            continue
        chosen.append(hit)
        used += cost
    return chosen
