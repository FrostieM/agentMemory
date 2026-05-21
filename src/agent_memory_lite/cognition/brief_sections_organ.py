"""Brief section builders for the v3 brain-organ layers — Hebbian
associates (Phase 2) and recent consolidation insights (Phase 3).

Extracted from cognition/brief.py during the v3.7 SLOC decomposition.
Every builder is failure-soft: a missing module or pre-migration DB
renders an empty section rather than breaking the brief.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.cognition.brief_models import BriefSection
from agent_memory_lite.cognition.brief_tokens import fit_to_budget
from agent_memory_lite.storage.reader import get_object, list_kind


def iso_now_for_brief() -> str:
    """Local import-free now() for the brief composer."""
    from agent_memory_lite.utils.time import iso_now  # noqa: PLC0415

    return iso_now()


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
        if d.get("status") != "active":
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
        if proj is None:
            continue
        gist = proj.get("gist") or proj.get("name") or proj.get("title") or "?"
        lines.append(f"- {node.kind}:{node.object_id} (assoc={node.activation:.2f}): {gist}")
        seen += 1
        if seen >= limit:
            break
    if seen == 0:
        return BriefSection(name="associates", budget=budget, lines=[])
    return BriefSection(name="associates", budget=budget, lines=fit_to_budget(lines, budget))


def _build_recent_insights(
    conn: sqlite3.Connection, workspace_id: str, budget: int, limit: int = 3
) -> BriefSection:
    """Surface top-N recent insight candidates (Phase 3).

    Reads ``status='candidate'`` insights with ``outcome_score >= 0``
    ordered by ``updated_at DESC``. Stamps ``last_surfaced_at`` and
    ``surface_count`` on the rows it surfaces -- the
    ``promote_insight_to_behavior`` job uses ``surface_count >= 2`` as
    the auto-promotion gate.
    """
    try:
        from agent_memory_lite.config.settings import get_settings  # noqa: PLC0415

        if not get_settings().consolidation_feedback_enabled:
            return BriefSection(name="recent_insights", budget=budget, lines=[])
    except Exception:  # pragma: no cover - defensive
        pass
    try:
        rows = conn.execute(
            """
            SELECT id, insight_type, summary, gist, confidence
              FROM insights
             WHERE workspace_id = ? AND status = 'candidate'
               AND COALESCE(outcome_score, 0.0) >= 0
             ORDER BY updated_at DESC
             LIMIT ?
            """,
            (workspace_id, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return BriefSection(name="recent_insights", budget=budget, lines=[])
    if not rows:
        return BriefSection(name="recent_insights", budget=budget, lines=[])
    lines = ["## Recent insights"]
    surfaced_ids: list[str] = []
    for row in rows:
        text = (row["gist"] or row["summary"] or "?")[:120]
        kind = row["insight_type"] or "insight"
        confidence = float(row["confidence"] or 0.0)
        lines.append(f"- {row['id']} [{kind}, conf={confidence:.2f}]: {text}")
        surfaced_ids.append(row["id"])
    # Best-effort stamp (failure-soft for pre-migration DBs).
    if surfaced_ids:
        now = iso_now_for_brief()
        try:
            placeholders = ", ".join("?" * len(surfaced_ids))
            conn.execute(
                f"UPDATE insights "
                f"SET last_surfaced_at = ?, surface_count = surface_count + 1 "
                f"WHERE workspace_id = ? AND id IN ({placeholders})",
                (now, workspace_id, *surfaced_ids),
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
    return BriefSection(name="recent_insights", budget=budget, lines=fit_to_budget(lines, budget))
