"""Brief section builders — identity + the v3.0-base "classic" sections
(top decisions, active task state, code hubs).

Extracted from cognition/brief.py during the v3.7 SLOC decomposition.
Each builder returns a BriefSection; an empty section (no lines) lets
compose_brief redistribute the freed budget to denser sections.
"""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.cognition.brief_models import BriefSection
from agent_memory_lite.cognition.brief_tokens import fit_to_budget
from agent_memory_lite.storage.reader import count_kind, list_kind

# Self-model narrative in the DB is 50-150 words; the brief renders an
# abridged version so workspace overview + discipline reminder still fit
# in the identity-section budget (~90 tokens at the default 500 budget).
_SELF_MODEL_BRIEF_WORDS = 40
_OPEN_TASK_STATUSES = ("active", "in_progress")

# Code-hub ranking is by caller count, which floats vendored libraries, minified
# assets, test scaffolding, and operational scripts to the top -- none are the
# load-bearing SOURCE hubs the section is meant to surface (the live workspace's
# top hubs were a vendored d3.min.js at 1511 callers and crash_test/seeds.py at
# 1140). Exclude them at read time only: the rows stay in code_digests for
# impact_check; just the brief view is filtered. SQL LIKE patterns over the
# POSIX-slash file_path column.
_CODE_HUB_PATH_EXCLUDES = (
    "%.min.js",
    "%.min.css",
    "%/vendor/%",
    "%/node_modules/%",
    "tests/%",
    "%/tests/%",
    "scripts/%",
)


def _blockers_count_from_json(value: object) -> int:
    if not isinstance(value, str) or not value.strip():
        return 0
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return 0
    return len(parsed) if isinstance(parsed, list) else 0


def _count_open_tasks(conn: sqlite3.Connection, workspace_id: str) -> int:
    try:
        return int(
            conn.execute(
                """
                SELECT COUNT(*) FROM tasks
                WHERE workspace_id = ? AND status IN ('active', 'in_progress')
                """,
                (workspace_id,),
            ).fetchone()[0]
        )
    except sqlite3.OperationalError:
        return 0


def _open_task_rows(
    conn: sqlite3.Connection, workspace_id: str, *, limit: int
) -> list[dict[str, object]]:
    try:
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE workspace_id = ? AND status IN ('active', 'in_progress')
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (workspace_id, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        item["blockers_count"] = _blockers_count_from_json(item.get("blockers_json"))
        out.append(item)
    return out


def _build_identity(conn: sqlite3.Connection, workspace_id: str, budget: int) -> BriefSection:
    """Identity layer: self-model + workspace counts + discipline reminder.

    The discipline reminder is the single most important line in the
    brief — it's how this closes the "agent knows about graph tools but
    still uses Read" gap.

    Phase 5: when a ``self_model`` row exists, prepend its
    ``identity_text`` so the agent's self-narrative is the FIRST thing
    on the page. Empty / missing row degrades gracefully.
    """
    # Sanitize workspace_id so a newline/tab in the id can't break the
    # brief's structural invariant (`lines[0]` = title, `lines[1]` =
    # identity text).
    safe_ws = " ".join(str(workspace_id).split())[:64] or "workspace"
    lines = [f"# {safe_ws}"]
    # Phase 5 self-model line goes immediately under the workspace title.
    # Gated on MEMORY_SELF_MODEL_ENABLED so off-path = byte-equivalent
    # to v3.0.0-base (workspace title without narrative).
    try:
        from agent_memory_lite.cognition.self_model import load_self_model  # noqa: PLC0415
        from agent_memory_lite.config.settings import get_settings  # noqa: PLC0415

        if get_settings().self_model_enabled:
            sm = load_self_model(conn, workspace_id=workspace_id)
            if sm is not None and sm.identity_text:
                # Brief budget for identity is tight (~90 tokens for the
                # whole section). Cap the narrative to _SELF_MODEL_BRIEF_WORDS
                # so the workspace-overview + discipline lines below still
                # fit. The full text lives in the DB for callers that
                # want the unabridged narrative.
                tokens = sm.identity_text.split()
                if len(tokens) > _SELF_MODEL_BRIEF_WORDS:
                    snippet = " ".join(tokens[:_SELF_MODEL_BRIEF_WORDS]).rstrip(".,;:") + "..."
                else:
                    snippet = " ".join(tokens)
                lines.append(snippet)
    except (ImportError, sqlite3.OperationalError):
        pass
    pinned_decisions = count_kind(
        conn, workspace_id=workspace_id, kind="decision", pinned_only=True
    )
    pinned_behaviors = count_kind(
        conn, workspace_id=workspace_id, kind="behavior", pinned_only=True
    )
    code_count = count_kind(conn, workspace_id=workspace_id, kind="code_digest")
    open_tasks = _count_open_tasks(conn, workspace_id)
    lines.append(
        f"Workspace overview: {pinned_decisions} pinned decisions, "
        f"{pinned_behaviors} pinned behaviors, {code_count} code digests, "
        f"{open_tasks} open tasks."
    )
    # Discipline reminder — only fires when there are code digests to
    # leverage. Empty workspaces skip the line (it would be noise).
    if code_count > 0:
        lines.append(
            "DISCIPLINE: before Read/Edit/Grep on any source file, "
            "call memory_impact_check(file_path=<path>) FIRST. "
            "It returns purpose + callers + verdict in one envelope; "
            "Read is fallback for understanding algorithm logic only."
        )
    return BriefSection(name="identity", budget=budget, lines=fit_to_budget(lines, budget))


def _build_top_decisions(
    conn: sqlite3.Connection, workspace_id: str, budget: int, limit: int = 5
) -> BriefSection:
    # Pull a wider window then filter to active rows -- archived /
    # superseded decisions must not surface in the brief's "Active
    # decisions" section. Phase 1 outcome-loop addition: also drop rows
    # whose outcome_score has fallen below zero. Pinned rows bypass the
    # filter (operator-marked invariants survive a temporary negative
    # outcome) but they still sort beneath neutral / positive peers.
    rows_raw = list_kind(conn, workspace_id=workspace_id, kind="decision", limit=limit * 6)
    rows = [
        d
        for d in rows_raw
        if d.get("status") == "active"
        and (d.get("pinned") or float(d.get("outcome_score") or 0.0) >= 0.0)
    ]
    # Sort: pinned first, then outcome_score, then recency.
    rows.sort(
        key=lambda d: (
            0 if d.get("pinned") else 1,
            -float(d.get("outcome_score") or 0.0),
        )
    )
    lines = ["## Active decisions"]
    for d in rows[:limit]:
        gist = d.get("gist") or d.get("title") or "?"
        marker = " (pinned)" if d.get("pinned") else ""
        sup = f" supersedes {d['supersedes']}" if d.get("supersedes") else ""
        lines.append(f"- {d['id']}{marker}: {gist}{sup}")
    return BriefSection(name="decisions", budget=budget, lines=fit_to_budget(lines, budget))


def _build_state(conn: sqlite3.Connection, workspace_id: str, budget: int) -> BriefSection:
    """Workspace-aware (P2): emit nothing on a workspace with no active
    tasks. The freed budget is reallocated by ``_redistribute_and_rebuild``
    in ``compose_brief``: when this section returns empty, denser
    sections (identity / behaviors / decisions / aging_decisions) get a
    proportional bonus + re-render with bigger caps.
    """
    rows = _open_task_rows(conn, workspace_id, limit=3)
    if not rows:
        return BriefSection(name="state", budget=budget, lines=[])
    lines = ["## State"]
    for t in rows:
        goal = t.get("goal_one_line") or "?"
        status = t.get("status", "?")
        next_action = t.get("next_action") or "(none)"
        blockers = t.get("blockers_count", 0)
        lines.append(
            f"- task {t.get('task_id', '?')} [{status}]: {goal} "
            f"→ next: {next_action} (blockers: {blockers})"
        )
    fitted = fit_to_budget(lines, budget)
    if not any(line.startswith("- task ") for line in fitted):
        return BriefSection(name="state", budget=budget, lines=[])
    return BriefSection(name="state", budget=budget, lines=fitted)


def _build_code_hubs(
    conn: sqlite3.Connection, workspace_id: str, budget: int, limit: int = 10
) -> BriefSection:
    """Workspace-aware (P2): emit nothing when the code-memory substrate
    is empty. Saves ~10 tokens on workspaces that don't use code-memory
    (non-software projects); the freed budget is rerouted by
    ``_redistribute_and_rebuild`` into the dense priority sections."""
    exclude_clause = " ".join("AND file_path NOT LIKE ?" for _ in _CODE_HUB_PATH_EXCLUDES)
    sql = (
        "SELECT * FROM code_digests WHERE workspace_id = ? "
        f"{exclude_clause} "
        "ORDER BY COALESCE(NULLIF(pagerank, 0), inbound_edge_count) DESC, "
        "  inbound_edge_count DESC "
        "LIMIT ?"
    )
    try:
        rows = conn.execute(sql, (workspace_id, *_CODE_HUB_PATH_EXCLUDES, limit)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if not rows:
        return BriefSection(name="code_hubs", budget=budget, lines=[])
    lines = ["## Code hubs"]
    for row in rows:
        path = row["file_path"]
        purpose = row["purpose_short"] or "(no digest)"
        callers = row["inbound_edge_count"]
        lines.append(f"- {path}: {purpose} ({callers} callers)")
    return BriefSection(name="code_hubs", budget=budget, lines=fit_to_budget(lines, budget))
