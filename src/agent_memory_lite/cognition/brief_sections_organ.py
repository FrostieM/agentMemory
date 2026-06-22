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
from agent_memory_lite.utils.insight_filters import is_low_signal_insight


def iso_now_for_brief() -> str:
    """Local import-free now() for the brief composer."""
    from agent_memory_lite.utils.time import iso_now  # noqa: PLC0415

    return iso_now()


# The canonical terminal/dead status DENYLIST for the knowledge kinds whose
# live states are many and dead states few: archived/superseded/rejected for
# decisions+insights, plus 'weakened' for theories (theories_repo flips a theory
# to 'weakened' on refuting evidence, and outcome_recompute / lint / self_model
# all bucket weakened WITH rejected as a hard-negative). A freshly-weakened
# theory's outcome_score is not recomputed negative until the next brain pass,
# so without the status arm it would slip through the outcome arm and surface as
# a positive associate in that window.
_DEAD_STATUSES = frozenset({"superseded", "archived", "rejected", "weakened"})

# The work-item kinds invert that: few live states, many terminal ones
# (done/cancelled/skipped/closed/fixed/wontfix/...). An ALLOWLIST of live
# statuses is safer than a denylist -- a terminal status added later is
# automatically dead. The live sets match the brief's own section filters
# (brief_sections_core/plan: tasks IN (active,in_progress); plan_steps IN
# (active,pending,blocked) per hygiene; brief_sections_watch: issues IN
# (open,in_progress)). 'blocked' is a LIVE plan-step state (a current obstacle),
# not terminal.
_LIVE_STATUS_BY_KIND: dict[str, frozenset[str]] = {
    "task": frozenset({"active", "in_progress"}),
    "plan_step": frozenset({"active", "pending", "blocked"}),
    "issue": frozenset({"open", "in_progress"}),
}


def _is_discredited(proj: dict[str, object]) -> bool:
    """A neighbor that must not be surfaced as a positive association. Covers
    every kind reachable in the associates substrate, by its terminal mechanism:

    * status DENYLIST (_DEAD_STATUSES) -- decision / theory / insight
    * status ALLOWLIST (_LIVE_STATUS_BY_KIND) -- task / plan_step / issue
    * active=0 -- the active-flag kinds behavior / skill / concept
    * is_archived=1 -- episode / chunk
    * non-pinned negative outcome_score -- the outcome-bearing kinds

    (code_digest carries no dead state -- it is hard-pruned when its file is
    deleted -- so it is intentionally never discredited here.) Status is
    case-folded (and whitespace-stripped) so a mixed-case or padded label
    cannot slip a dead row through, nor over-filter a padded live one."""
    status = str(proj.get("status") or "").strip().lower()
    # Knowledge kinds: terminal-status denylist.
    if status in _DEAD_STATUSES:
        return True
    # active-flag kinds (behavior/skill/concept): active=0 is terminal. Their
    # projections emit 'active'; kinds without an active flag return None here,
    # which this skips. A deactivated row can keep its default 0.0 outcome_score
    # (e.g. a behavior auto-archived for never firing, or an archived concept/
    # skill), so the outcome arm below would not catch it.
    if proj.get("active") is False:
        return True
    # episode/chunk: is_archived=1 is terminal (those tables have no status).
    if proj.get("is_archived") is True:
        return True
    # Work-item kinds: any status outside the live allowlist is terminal.
    kind = proj.get("kind")
    if isinstance(kind, str):
        live = _LIVE_STATUS_BY_KIND.get(kind)
        if live is not None and status not in live:
            return True
    # Terminal checks precede the pinned bypass (a pinned-but-dead row is still
    # dead); the outcome arm is last.
    if proj.get("pinned"):
        return False
    raw = proj.get("outcome_score")
    return isinstance(raw, (int, float)) and float(raw) < 0.0


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


def _build_recent_insights(
    conn: sqlite3.Connection, workspace_id: str, budget: int, limit: int = 3
) -> BriefSection:
    """Surface top-N recent insight candidates (Phase 3).

    Reads ``status='candidate'`` insights with ``outcome_score >= 0``
    ordered by ``updated_at DESC``. Low-signal file-indexing placeholders
    are skipped before surfacing. Stamps ``last_surfaced_at`` and
    ``surface_count`` on the rows it surfaces -- the promotion job uses
    ``surface_count >= 2`` as the auto-promotion gate.
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
            SELECT id, insight_type, summary, gist, tags_json, confidence
              FROM insights
             WHERE workspace_id = ? AND status = 'candidate'
               AND COALESCE(outcome_score, 0.0) >= 0
             ORDER BY updated_at DESC
            """,
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return BriefSection(name="recent_insights", budget=budget, lines=[])
    useful_rows = [
        row
        for row in rows
        if not is_low_signal_insight(row["summary"], row["gist"], row["tags_json"])
    ][:limit]
    if not useful_rows:
        return BriefSection(name="recent_insights", budget=budget, lines=[])
    lines = ["## Recent insights"]
    surfaced_ids: list[str] = []
    for row in useful_rows:
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


def _build_lessons(
    conn: sqlite3.Connection, workspace_id: str, budget: int, limit: int = 2
) -> BriefSection:
    """Surface operator-reviewed ``lesson`` insights (Phase 3 adoption gap).

    Promoted lessons land at ``status='new'`` then ``'accepted'`` -- they skip
    the ``'candidate'`` status that ``_build_recent_insights`` filters on, so
    without a dedicated section the highest-confidence reviewed lessons
    (0.85-0.95) never surface while raw 0.55 consolidation candidates do. Kept
    separate from recent_insights so surfacing a reviewed lesson does not inflate
    the candidate ``surface_count`` that gates auto-promotion. Tightly capped
    (limit=2) and low-signal-filtered. ROTATED by least-recently-surfaced
    (``last_surfaced_at`` ascending, NULL/never-surfaced first; confidence as the
    tiebreak), so every reviewed lesson cycles through the brief over successive
    sessions instead of only the top-2 by confidence surfacing forever (the
    section stamps last_surfaced_at, so the rotation is self-advancing).
    Failure-soft.
    """
    try:
        rows = conn.execute(
            """
            SELECT id, insight_type, summary, gist, tags_json, confidence
              FROM insights
             WHERE workspace_id = ? AND insight_type = 'lesson'
               AND status IN ('accepted', 'new')
               AND COALESCE(outcome_score, 0.0) >= 0
             ORDER BY last_surfaced_at ASC, confidence DESC, updated_at DESC
            """,
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return BriefSection(name="lessons", budget=budget, lines=[])
    useful_rows = [
        row
        for row in rows
        if not is_low_signal_insight(row["summary"], row["gist"], row["tags_json"])
    ][:limit]
    if not useful_rows:
        return BriefSection(name="lessons", budget=budget, lines=[])
    lines = ["## Lessons learned"]
    surfaced_ids: list[str] = []
    for row in useful_rows:
        text = (row["gist"] or row["summary"] or "?")[:120]
        confidence = float(row["confidence"] or 0.0)
        lines.append(f"- {row['id']} [lesson, conf={confidence:.2f}]: {text}")
        surfaced_ids.append(row["id"])
    # Best-effort stamp so the 0-surface metric reflects reality. These are
    # already accepted, so surface_count no longer gates promotion for them.
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
    fitted = fit_to_budget(lines, budget)
    # If only the header survived the budget (no bullet fit), render nothing -- a
    # bare "## Lessons learned" is noise, and an empty section frees its budget to
    # the priority recipients.
    if len(fitted) <= 1:
        return BriefSection(name="lessons", budget=budget, lines=[])
    return BriefSection(name="lessons", budget=budget, lines=fitted)
