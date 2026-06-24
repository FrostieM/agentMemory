"""Brief section builders for the v3 brain-organ layers — Hebbian
associates (Phase 2) and recent consolidation insights (Phase 3).

Extracted from cognition/brief.py during the v3.7 SLOC decomposition.
Every builder is failure-soft: a missing module or pre-migration DB
renders an empty section rather than breaking the brief.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.cognition.brief_models import BriefSection
from agent_memory_lite.cognition.brief_sections_organ_discredit import (
    _DEAD_STATUSES,
    _LIVE_STATUS_BY_KIND,
    _is_discredited,
)
from agent_memory_lite.cognition.brief_sections_organ_insights import (
    _build_lessons,
    _build_recent_insights,
    iso_now_for_brief,
)
from agent_memory_lite.cognition.brief_tokens import fit_to_budget
from agent_memory_lite.storage.reader import get_object, list_kind

__all__ = [
    "_DEAD_STATUSES",
    "_LIVE_STATUS_BY_KIND",
    "_build_associates",
    "_build_lessons",
    "_build_recent_insights",
    "_is_discredited",
    "iso_now_for_brief",
]


def _build_associates(
    conn: sqlite3.Connection, workspace_id: str, budget: int, limit: int = 3
) -> BriefSection:
    """Top-N Hebbian-associated rows of the workspace's active decisions.

    Phase 2 surfaces the strongest cross-kind associations so the agent
    sees what fires together with current invariants. Seeds = top-3
    active+positive decisions; the spreading-activation reader walks
    soft_edges one hop and ranks neighbours by accumulated weight.
    """
    try:
        from agent_memory_lite.retrieval.spreading_activation import spread  # noqa: PLC0415
    except ImportError:
        return BriefSection(name="associates", budget=budget, lines=[])
    seed_rows = list_kind(conn, workspace_id=workspace_id, kind="decision", limit=limit * 4)
    seeds: list[tuple[str, str, float]] = []
    for d in seed_rows:
        if str(d.get("status") or "").strip().lower() != "active":
            continue
        if not d.get("pinned") and float(d.get("outcome_score") or 0.0) < 0.0:
            continue
        seeds.append(("decision", str(d["id"]), 1.0))
        if len(seeds) >= 3:
            break
    if not seeds:
        return BriefSection(name="associates", budget=budget, lines=[])
    activations = spread(
        conn,
        workspace_id=workspace_id,
        seeds=seeds,
        max_hops=1,
        max_nodes=limit * 4,
    )
    if not activations:
        return BriefSection(name="associates", budget=budget, lines=[])
    seed_ids = {(k, i) for k, i, _ in seeds}
    lines = ["## Associated to current decisions"]
    seen = 0
    for node in activations:
        if (node.kind, node.object_id) in seed_ids:
            continue
        proj = get_object(conn, workspace_id=workspace_id, kind=node.kind, object_id=node.object_id)
        # Skip discredited neighbors: a terminal-status row (superseded/archived/
        # rejected/weakened) or a non-pinned negative-outcome row is dead signal
        # that must not be shown as a positive association. The negative-outcome
        # ones the SAME brief also lists under watch-outs, so surfacing them here
        # too is a self-contradiction the agent reads. Mirrors the seed filter.
        if proj is None or _is_discredited(proj):
            continue
        gist = proj.get("gist") or proj.get("name") or proj.get("title") or "?"
        lines.append(f"- {node.kind}:{node.object_id} (assoc={node.activation:.2f}): {gist}")
        seen += 1
        if seen >= limit:
            break
    if seen == 0:
        return BriefSection(name="associates", budget=budget, lines=[])
    return BriefSection(name="associates", budget=budget, lines=fit_to_budget(lines, budget))
