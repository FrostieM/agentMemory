"""Time-based confidence decay for chunk hits.

When ``MEMORY_CONFIDENCE_DECAY_ENABLED=1`` the score pipeline
multiplies each hit's score by ``exp(-age_days / half_life)`` so
old chunks don't out-rank fresh work just because their FTS keywords
match. Half-life is configurable; an item ``half_life_days`` old
gets multiplied by 0.5, two half-lives by 0.25, etc.

Off by default - the existing score weights remain authoritative
unless the operator opts in.
"""

from __future__ import annotations

import math
from datetime import UTC

from agent_memory_lite.config.settings import Settings, get_settings
from agent_memory_lite.models.retrieval import ScoredHit
from agent_memory_lite.utils.time import now, parse_iso


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
    delta = (current - created).total_seconds() / 86400.0
    return max(0.0, delta)


def apply_confidence_decay(
    hits: list[ScoredHit],
    *,
    settings: Settings | None = None,
) -> list[ScoredHit]:
    """Return hits with score multiplied by an exponential age decay.

    No-op when the env flag is off. Re-sorts results because decay
    can re-order hits that started with similar scores.
    """
    cfg = settings if settings is not None else get_settings()
    if not cfg.confidence_decay_enabled:
        return hits
    half_life = max(1e-3, cfg.confidence_decay_half_life_days)
    out: list[ScoredHit] = []
    for hit in hits:
        age = _hit_age_days(hit)
        if age is None:
            out.append(hit)
            continue
        factor = math.pow(0.5, age / half_life)
        metadata = dict(hit.metadata)
        metadata["confidence_decay_factor"] = round(factor, 4)
        metadata["confidence_decay_age_days"] = round(age, 2)
        out.append(hit.model_copy(update={"score": hit.score * factor, "metadata": metadata}))
    out.sort(key=lambda item: item.score, reverse=True)
    return out
