"""Brief section builders for the watch layers — blindspots (v3.1
Vector 3), aging decisions, and low-outcome watch-outs (Phase 1).

Extracted from cognition/brief.py during the v3.7 SLOC decomposition.
Every builder is failure-soft: a missing module or pre-migration DB
renders an empty section rather than breaking the brief.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.cognition.brief_models import BriefSection
from agent_memory_lite.cognition.brief_tokens import fit_to_budget


def _build_blindspots(conn: sqlite3.Connection, workspace_id: str, budget: int) -> BriefSection:
    """Surface tokens present in >=N episodes but in ZERO active decisions.

    v3.1 Vector 3: structural asymmetry detection. The agent sees
    "discussed-but-not-decided" topics on session start so it can
    propose a decision (or, if the asymmetry is by design, a concept).
    Failure-soft — missing module or pre-migration DB renders empty.
    """
    try:
        from agent_memory_lite.maintenance.blindspot_detection import (  # noqa: PLC0415
            find_blindspots,
            is_enabled,
        )

        if not is_enabled():
            return BriefSection(name="blindspots", budget=budget, lines=[])
        rows = find_blindspots(conn, workspace_id=workspace_id, enrich=False)
    except Exception:  # pragma: no cover - defensive
        return BriefSection(name="blindspots", budget=budget, lines=[])
    if not rows:
        return BriefSection(name="blindspots", budget=budget, lines=[])
    lines = ["## Blindspots (discussed but no decision)"]
    for row in rows:
        # Surface the LLM-augmented description when v3.1 Vector 3 LLM is
        # enabled. Trimmed to ~120 chars so the section stays inside its
        # budget — the full description remains accessible via the
        # underlying ``find_blindspots`` API for callers with more room.
        if row.description:
            desc = row.description[:120].rstrip()
            if len(row.description) > 120:
                desc = desc + "..."
            lines.append(f"- {row.token!r} ({row.episode_count} ep): {desc}")
        else:
            lines.append(f"- {row.token!r}: {row.episode_count} episodes, 0 decisions")
    return BriefSection(name="blindspots", budget=budget, lines=fit_to_budget(lines, budget))


def _build_aging_decisions(
    conn: sqlite3.Connection, workspace_id: str, budget: int
) -> BriefSection:
    """Surface decisions older than the aging threshold with zero feedback.

    Failure-soft: when the aging module is disabled, or the DB lacks the
    ``outcome_score`` column (pre-Phase-1), the section renders empty
    and adds no bytes to the brief.
    """
    try:
        from agent_memory_lite.maintenance.aging_decisions import (  # noqa: PLC0415
            find_aging_decisions,
            is_enabled,
        )

        if not is_enabled():
            return BriefSection(name="aging_decisions", budget=budget, lines=[])
        rows = find_aging_decisions(conn, workspace_id=workspace_id)
    except Exception:  # pragma: no cover - defensive
        return BriefSection(name="aging_decisions", budget=budget, lines=[])
    if not rows:
        return BriefSection(name="aging_decisions", budget=budget, lines=[])
    lines = ["## Aging decisions (no feedback yet)"]
    for row in rows:
        title = row.title[:60] or "?"
        lines.append(f"- {row.decision_id} ({row.age_days}d, conf={row.confidence:.2f}): {title}")
    return BriefSection(name="aging_decisions", budget=budget, lines=fit_to_budget(lines, budget))


def _build_watch_outs(
    conn: sqlite3.Connection, workspace_id: str, budget: int, limit: int = 3
) -> BriefSection:
    """Top-N rows with the lowest outcome_score across watched kinds.

    Surfaces failed approaches so the agent does not propose them again.
    Phase 1 pools decisions, theories, and behaviors. v3.5: the
    ``status != 'archived'`` filter keeps operator-dispositioned rows
    from re-surfacing noise on every brief.
    """
    # Hand-stitched UNION rather than reader.list_kind because we want a
    # single cross-kind ORDER BY outcome_score ASC pass.
    sql = """
        SELECT id, 'decision' AS kind, COALESCE(gist, title, '') AS gist,
               outcome_score, status
          FROM decisions
         WHERE workspace_id = ? AND outcome_score < 0
           AND status != 'archived'
        UNION ALL
        SELECT id, 'theory' AS kind, COALESCE(gist, title, claim, '') AS gist,
               outcome_score, status
          FROM theories
         WHERE workspace_id = ? AND outcome_score < 0
           AND status != 'archived'
        UNION ALL
        SELECT id, 'behavior' AS kind, COALESCE(rule_one_line, name, '') AS gist,
               outcome_score, NULL AS status
          FROM behaviors
         WHERE workspace_id = ? AND outcome_score < 0
           AND active = 1
        ORDER BY outcome_score ASC
        LIMIT ?
    """
    try:
        rows = conn.execute(sql, (workspace_id, workspace_id, workspace_id, limit)).fetchall()
    except sqlite3.OperationalError:
        # Pre-migration DB; outcome_score column missing.
        rows = []
    lines = ["## Watch-outs"] if rows else []
    for row in rows:
        gist = (row["gist"] or "?")[:70]
        score = float(row["outcome_score"] or 0.0)
        lines.append(f"- {row['kind']}:{row['id']} (outcome={score:.1f}): {gist}")
    return BriefSection(name="watch_outs", budget=budget, lines=fit_to_budget(lines, budget))
