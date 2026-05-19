"""Blindspot LLM-enrichment helper.

Lifted out of ``blindspot_detection.py`` to keep that module under the
150-SLOC ceiling after adding the v3.1 Vector 3 LLM augmentation.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_memory_lite.maintenance.blindspot_detection import Blindspot


def maybe_enrich_with_llm(
    rows: list[Blindspot],
    ep_rows: list[sqlite3.Row],
) -> list[Blindspot]:
    """Add a one-sentence description to each blindspot when the LLM
    augmentation is enabled. Empty string fallback keeps the surface
    intact when Ollama is offline.
    """
    if not rows:
        return rows
    from agent_memory_lite.maintenance.blindspot_detection import Blindspot  # noqa: PLC0415
    from agent_memory_lite.maintenance.blindspot_llm import (  # noqa: PLC0415
        is_llm_enabled,
        llm_describe_blindspot,
    )

    if not is_llm_enabled():
        return rows
    try:
        from agent_memory_lite.config.settings import get_settings  # noqa: PLC0415

        settings = get_settings()
        base_url = str(getattr(settings, "llm_base_url", "") or "")
        model = str(getattr(settings, "llm_model", "") or "")
    except Exception:  # pragma: no cover - defensive
        return rows
    excerpts = [
        str(r["raw_text"] if isinstance(r, sqlite3.Row) else r[0])[:200] for r in ep_rows[:6]
    ]
    enriched: list[Blindspot] = []
    for row in rows:
        desc = llm_describe_blindspot(
            token=row.token,
            episode_count=row.episode_count,
            sample_excerpts=excerpts,
            base_url=base_url,
            model=model,
        )
        enriched.append(
            Blindspot(
                token=row.token,
                episode_count=row.episode_count,
                decision_count=row.decision_count,
                description=desc or "",
            )
        )
    return enriched
