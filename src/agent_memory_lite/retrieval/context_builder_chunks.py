"""Chunk filtering, stub rescue, and usage-feedback boosting.

Pulled from ``context_builder.py`` so the chunk pipeline (mojibake
cleanup, low-confidence drop, exact-FTS stub rescue, feedback boosts)
can be tested and modified without scrolling through the structured-
section rendering.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC

from agent_memory_lite.maintenance.usage_feedback import chunk_feedback_boosts
from agent_memory_lite.models.retrieval import ScoredHit
from agent_memory_lite.retrieval.context_builder_constants import (
    KEEP_EXACT_FTS_RANK_BELOW,
    LOW_CONFIDENCE_RECENT_SCORE,
    LOW_CONFIDENCE_STALE_DAYS,
    LOW_CONFIDENCE_STALE_SCORE,
    LOW_CONFIDENCE_VECTOR_SCORE,
    MAX_CHUNK_TEXT_CHARS,
    MAX_STUB_CHUNK_TEXT_CHARS,
    MOJIBAKE_REPLACEMENT_MIN,
    MOJIBAKE_REPLACEMENT_RATIO,
)
from agent_memory_lite.retrieval.context_builder_text import _clip_text
from agent_memory_lite.retrieval.token_budget import fit_within_budget
from agent_memory_lite.utils.time import now, parse_iso
from agent_memory_lite.utils.tokens import estimate_tokens


def _clip_hits_for_context(hits: list[ScoredHit]) -> list[ScoredHit]:
    return [
        hit.model_copy(update={"text": _clip_text(hit.text, MAX_CHUNK_TEXT_CHARS)}) for hit in hits
    ]


def _stub_hit_for_context(hit: ScoredHit) -> ScoredHit:
    metadata = dict(hit.metadata)
    metadata["render_level"] = "stub"
    metadata["why_relevant"] = "exact fts match preserved under tight context budget"
    return hit.model_copy(
        update={
            "text": _clip_text(hit.text, MAX_STUB_CHUNK_TEXT_CHARS),
            "metadata": metadata,
        }
    )


def _ensure_exact_hit_stub(
    hits: list[ScoredHit],
    *,
    chosen: list[ScoredHit],
    max_tokens: int,
) -> list[ScoredHit]:
    exact_hits = [hit for hit in hits if _is_exact_fts_hit(hit)]
    if not exact_hits:
        return chosen
    chosen_ids = {hit.id for hit in chosen}
    if any(hit.id in chosen_ids for hit in exact_hits):
        return chosen

    stub = _stub_hit_for_context(exact_hits[0])
    rescue_budget = max(max_tokens, estimate_tokens(stub.text) + 32)
    rescued = fit_within_budget([stub, *chosen], max_tokens=rescue_budget)
    rescued_ids = {hit.id for hit in rescued}
    if stub.id in rescued_ids:
        return rescued
    return [stub, *chosen]


def _has_mojibake_noise(text: str) -> bool:
    replacements = text.count("�")
    if replacements < MOJIBAKE_REPLACEMENT_MIN:
        return False
    return replacements / max(len(text), 1) >= MOJIBAKE_REPLACEMENT_RATIO


def _hit_age_days(hit: ScoredHit) -> float | None:
    created_at = hit.metadata.get("created_at")
    if not isinstance(created_at, str) or not created_at:
        return None
    try:
        created = parse_iso(created_at)
    except ValueError:
        return None
    current = now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return max(0.0, (current - created).total_seconds() / 86400.0)


def _is_exact_fts_hit(hit: ScoredHit) -> bool:
    rank = hit.metadata.get("fts_rank")
    return isinstance(rank, int) and 0 <= rank < KEEP_EXACT_FTS_RANK_BELOW


def _keep_context_hit(hit: ScoredHit, *, historical: bool) -> bool:
    if historical:
        keep = True
    elif _has_mojibake_noise(hit.text):
        keep = False
    elif _is_exact_fts_hit(hit) or hit.score >= LOW_CONFIDENCE_STALE_SCORE:
        keep = True
    else:
        age_days = _hit_age_days(hit)
        keep = hit.score >= LOW_CONFIDENCE_RECENT_SCORE or (
            "vector" in hit.sources and hit.score >= LOW_CONFIDENCE_VECTOR_SCORE
        )
        if age_days is not None and age_days > LOW_CONFIDENCE_STALE_DAYS:
            keep = False
    return keep


def filter_context_hits(hits: list[ScoredHit], *, historical: bool) -> list[ScoredHit]:
    """Suppress stale low-confidence chunk noise before the context budget pass.

    Exact FTS top hits are preserved even when old. Historical contexts also
    preserve all hits because the caller explicitly requested old decisions and
    evidence.
    """

    return [hit for hit in hits if _keep_context_hit(hit, historical=historical)]


def _apply_usage_feedback(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    hits: list[ScoredHit],
) -> list[ScoredHit]:
    boosts = chunk_feedback_boosts(
        conn,
        workspace_id=workspace_id,
        chunk_ids=[hit.id for hit in hits],
    )
    if not boosts:
        return hits
    adjusted: list[ScoredHit] = []
    for hit in hits:
        boost = boosts.get(hit.id, 0.0)
        if boost == 0.0:
            adjusted.append(hit)
            continue
        metadata = dict(hit.metadata)
        metadata["usage_feedback_boost"] = round(boost, 4)
        adjusted.append(hit.model_copy(update={"score": hit.score + boost, "metadata": metadata}))
    adjusted.sort(key=lambda item: item.score, reverse=True)
    return adjusted
