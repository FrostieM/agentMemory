"""memory_brief — ≤500-token session-start brief composed from compact projections.

The killer feature. Replaces the verbose v2 ``<memory_context>`` envelope
with a tight pre-task brief assembled from SQL projections — no full
markdown bodies, no LLM call on the hot path.

Composition (token budget per layer, target):

  identity      100  workspace name + invariants + project_brief
  behaviors     120  pinned behaviors, rule_one_line each
  decisions     130  top-5 active decisions by recency / pinned
  state          60  active task: goal_one_line + next_action
  code hubs      90  top-10 code_digests by pagerank, purpose_short each

Total target ≤500 tokens. Tokens counted by a cheap whitespace split
(approximation; close to tiktoken cl100k_base for ASCII text).

Cached on workspace fingerprint (hash of pinned-file shas + active
task updated_at). Cache hit ~5ms, miss ~80ms (pure SQL).
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from agent_memory_lite.storage.reader import (
    count_kind,
    get_object,
    list_kind,
)

DEFAULT_TOKEN_BUDGET = 500

# Self-model narrative in the DB is 50-150 words; the brief renders an
# abridged version so workspace overview + discipline reminder still fit
# in the identity-section budget (~90 tokens at the default 500 budget).
_SELF_MODEL_BRIEF_WORDS = 40

# In-process brief cache, keyed on (workspace_id, max_tokens, fingerprint).
# Fingerprint is a hash of cardinal "last write" timestamps for the kinds
# that contribute to the brief — invalidates automatically on any
# mutation in those tables. Bounded to 16 entries; eviction is LRU via
# dict insertion order.
_BRIEF_CACHE_MAX = 16
_BRIEF_CACHE: dict[tuple[str, int, str], Brief] = {}


@dataclass(frozen=True, slots=True)
class BriefSection:
    """One render-able section of the brief with its budget + lines."""

    name: str
    budget: int
    lines: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Brief:
    """Final brief — markdown body + composition stats."""

    body_md: str
    token_count: int
    sections: list[BriefSection]
    cache_hit: bool = False


# ============================================================
# Token approximation
# ============================================================


def approx_tokens(text: str) -> int:
    """Whitespace-split token count. Close enough to cl100k for budgeting."""
    if not text:
        return 0
    return len(text.split())


def fit_to_budget(lines: list[str], budget: int) -> list[str]:
    """Keep lines that fit within ``budget``; skip oversized lines and keep going.

    Earlier semantics broke on the first overflowing line, which meant a
    single oversized line (e.g. a long self-model narrative) silently
    nuked every subsequent line in the section. The new semantics:
    skip the line that doesn't fit and try the next -- shorter trailing
    lines (e.g. workspace overview + discipline reminder) still render.
    Lines are kept in input order; the only difference is that overflow
    no longer terminates the loop.
    """
    out: list[str] = []
    used = 0
    for line in lines:
        cost = approx_tokens(line)
        if used + cost > budget:
            continue
        out.append(line)
        used += cost
    return out


# ============================================================
# Section builders
# ============================================================


def _build_identity(conn: sqlite3.Connection, workspace_id: str, budget: int) -> BriefSection:
    """Identity layer: self-model + workspace counts + discipline reminder.

    The discipline reminder is the single most important line in the
    brief — it's how this closes the "agent knows about graph tools but
    still uses Read" gap. The line stays foreground in every session,
    so the agent's first instinct on a new file is impact_check, not
    Read.

    Phase 5: when a ``self_model`` row exists, prepend its
    ``identity_text`` so the agent's self-narrative is the FIRST thing
    on the page. Empty / missing row degrades gracefully -- the
    workspace name + counts + discipline line still render.
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
    open_tasks = count_kind(conn, workspace_id=workspace_id, kind="task", status="in_progress")
    lines.append(
        f"Workspace overview: {pinned_decisions} pinned decisions, "
        f"{pinned_behaviors} pinned behaviors, {code_count} code digests, "
        f"{open_tasks} in-progress tasks."
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


def _build_pinned_behaviors(
    conn: sqlite3.Connection, workspace_id: str, budget: int
) -> BriefSection:
    rows = list_kind(conn, workspace_id=workspace_id, kind="behavior", pinned_only=True, limit=12)
    lines = ["## Pinned behaviors"]
    for b in rows:
        rule = b.get("rule_one_line") or b.get("name") or ""
        applies = b.get("applies_to_csv") or ""
        line = f"- {b.get('name', '?')}: {rule}"
        if applies:
            line += f" (applies_to: {applies})"
        lines.append(line)
    return BriefSection(name="behaviors", budget=budget, lines=fit_to_budget(lines, budget))


def _build_top_decisions(
    conn: sqlite3.Connection, workspace_id: str, budget: int, limit: int = 5
) -> BriefSection:
    # Pull a wider window then filter to active rows -- archived /
    # superseded decisions must not surface in the brief's "Active
    # decisions" section. Without the filter, an old smoke-test
    # row pinned then archived would still show up under "Active".
    #
    # Phase 1 outcome-loop addition: also drop rows whose outcome_score
    # has fallen below zero. Pinned rows bypass the filter (operator-
    # marked invariants survive a temporary negative outcome) but they
    # still sort beneath neutral / positive peers.
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


def _build_associates(
    conn: sqlite3.Connection, workspace_id: str, budget: int, limit: int = 3
) -> BriefSection:
    """Top-N Hebbian-associated rows of the workspace's active decisions.

    Phase 2 surfaces the strongest cross-kind associations so the agent
    sees what fires together with current invariants. Seeds = top-3
    active+positive decisions; the spreading-activation reader walks
    soft_edges one hop and ranks neighbours by accumulated weight.

    Empty section (no lines) when:
    - no active decisions to seed from
    - no soft_edges from those seeds
    - retrieval/spreading_activation cannot import (pre-migration code)
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

    Empty section (no lines) when:
    - MEMORY_CONSOLIDATION_FEEDBACK_ENABLED is false (off-path)
    - no candidate insights
    - migration 0004 not yet applied (no last_surfaced_at column)
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


def iso_now_for_brief() -> str:
    """Local import-free now() for the brief composer."""
    from agent_memory_lite.utils.time import iso_now  # noqa: PLC0415

    return iso_now()


def _build_watch_outs(
    conn: sqlite3.Connection, workspace_id: str, budget: int, limit: int = 3
) -> BriefSection:
    """Top-N rows with the lowest outcome_score across watched kinds.

    Surfaces failed approaches so the agent does not propose them again.
    Phase 1 puts decisions, theories, and behaviors into the same pool;
    later phases (consolidation feedback, recall) may extend.
    """
    # Hand-stitched UNION rather than reader.list_kind because we want a
    # single cross-kind ORDER BY outcome_score ASC pass.
    sql = """
        SELECT id, 'decision' AS kind, COALESCE(gist, title, '') AS gist,
               outcome_score, status
          FROM decisions
         WHERE workspace_id = ? AND outcome_score < 0
        UNION ALL
        SELECT id, 'theory' AS kind, COALESCE(gist, title, claim, '') AS gist,
               outcome_score, status
          FROM theories
         WHERE workspace_id = ? AND outcome_score < 0
        UNION ALL
        SELECT id, 'behavior' AS kind, COALESCE(rule_one_line, name, '') AS gist,
               outcome_score, NULL AS status
          FROM behaviors
         WHERE workspace_id = ? AND outcome_score < 0
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


def _build_state(conn: sqlite3.Connection, workspace_id: str, budget: int) -> BriefSection:
    rows = list_kind(conn, workspace_id=workspace_id, kind="task", limit=3)
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
    if len(lines) == 1:
        lines.append("- no active tasks")
    return BriefSection(name="state", budget=budget, lines=fit_to_budget(lines, budget))


def _build_code_hubs(
    conn: sqlite3.Connection, workspace_id: str, budget: int, limit: int = 10
) -> BriefSection:
    """Top-N code digests by pagerank (or inbound_edge_count fallback)."""
    sql = (
        "SELECT * FROM code_digests WHERE workspace_id = ? "
        "ORDER BY COALESCE(NULLIF(pagerank, 0), inbound_edge_count) DESC, "
        "  inbound_edge_count DESC "
        "LIMIT ?"
    )
    try:
        rows = conn.execute(sql, (workspace_id, limit)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    lines = ["## Code hubs"]
    for row in rows:
        path = row["file_path"]
        purpose = row["purpose_short"] or "(no digest)"
        callers = row["inbound_edge_count"]
        lines.append(f"- {path}: {purpose} ({callers} callers)")
    if len(lines) == 1:
        lines.append("- no code digests indexed yet")
    return BriefSection(name="code_hubs", budget=budget, lines=fit_to_budget(lines, budget))


# ============================================================
# Top-level composer
# ============================================================


def _workspace_fingerprint(conn: sqlite3.Connection, workspace_id: str) -> str:
    """Hash the cardinal mutation timestamps that affect the brief.

    Reads ``MAX(updated_at)`` from decisions / behaviors / tasks /
    code_digests and ``MAX(created_at)`` from episodes for the
    workspace. Any write in those tables changes at least one of those
    maxima, so the fingerprint flips → cache miss on next call.

    Returns the first 12 chars of SHA1 (collision-safe at this cache
    size). On any SQL error returns a unique sentinel so the call
    bypasses the cache instead of returning stale content.

    Implementation note (bug fix 2026-05-18): an earlier revision used
    five chained ``LEFT JOIN``s on a single placeholder row, which
    becomes a Cartesian product over decisions x behaviors x tasks x
    code_digests x episodes. On a moderately busy workspace (copyBot:
    248 x 3 x 1 x 1355 x 974 ~ 982 million synthetic rows) the
    fingerprint took ~2 minutes -- long enough that the memory brief hook
    timed out before ever rendering. UNION ALL over five small index-
    seek scans returns the same answer in < 5 ms.
    """
    try:
        rows = conn.execute(
            """
            SELECT MAX(ts) FROM (
              SELECT updated_at AS ts FROM decisions     WHERE workspace_id = ?
              UNION ALL
              SELECT updated_at        FROM behaviors    WHERE workspace_id = ?
              UNION ALL
              SELECT updated_at        FROM tasks        WHERE workspace_id = ?
              UNION ALL
              SELECT updated_at        FROM code_digests WHERE workspace_id = ?
              UNION ALL
              SELECT created_at        FROM episodes     WHERE workspace_id = ?
            )
            """,
            (workspace_id, workspace_id, workspace_id, workspace_id, workspace_id),
        ).fetchone()
    except sqlite3.Error:
        return f"err-{workspace_id}-{id(conn)}"
    if rows is None:
        return f"empty-{workspace_id}"
    raw = str(rows[0] or "")
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def _cache_remember(key: tuple[str, int, str], brief: Brief) -> None:
    """LRU-style insert into the bounded brief cache."""
    _BRIEF_CACHE.pop(key, None)
    _BRIEF_CACHE[key] = brief
    while len(_BRIEF_CACHE) > _BRIEF_CACHE_MAX:
        # Pop oldest (insertion-order = LRU because we del+set on hit).
        oldest = next(iter(_BRIEF_CACHE))
        del _BRIEF_CACHE[oldest]


def compose_brief(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    task: str | None = None,
    max_tokens: int = DEFAULT_TOKEN_BUDGET,
) -> Brief:
    """Build the brief for a workspace at session start.

    ``task`` is an optional natural-language task description; reserved
    for future task-biased section selection (e.g. rank decisions by
    overlap with task tokens). v1 ignores it and returns the static
    brief.

    Cached on (workspace_id, max_tokens, workspace_fingerprint). Cache
    hits return ``Brief(cache_hit=True)`` in ~sub-millisecond. The
    fingerprint flips on any decision / behavior / task / code_digest /
    episode mutation, so a stale cache cannot survive a write.
    """
    del task  # reserved; future task-biased ranking
    fingerprint = _workspace_fingerprint(conn, workspace_id)
    cache_key = (workspace_id, max_tokens, fingerprint)
    cached = _BRIEF_CACHE.get(cache_key)
    if cached is not None:
        # Refresh LRU position so frequently-used briefs stay hot.
        del _BRIEF_CACHE[cache_key]
        _BRIEF_CACHE[cache_key] = cached
        # Return a new Brief with cache_hit=True; the underlying body
        # is identical (Brief is frozen so this is cheap).
        return Brief(
            body_md=cached.body_md,
            token_count=cached.token_count,
            sections=cached.sections,
            cache_hit=True,
        )
    # Per-section budget allocation (target ratios — sum to max_tokens).
    # ``watch_outs`` (Phase 1), ``associates`` (Phase 2), and
    # ``recent_insights`` (Phase 3) each take ~6-8% and all stay empty on
    # a fresh workspace, so the brief footprint for an empty DB is byte-
    # equivalent to the v3.0.0-base baseline.
    weights = {
        "identity": 0.18,
        "behaviors": 0.20,
        "decisions": 0.22,
        "state": 0.12,
        "code_hubs": 0.10,
        "associates": 0.06,
        "recent_insights": 0.06,
        "watch_outs": 0.06,
    }
    budgets = {name: int(max_tokens * w) for name, w in weights.items()}

    sections = [
        _build_identity(conn, workspace_id, budgets["identity"]),
        _build_pinned_behaviors(conn, workspace_id, budgets["behaviors"]),
        _build_top_decisions(conn, workspace_id, budgets["decisions"]),
        _build_state(conn, workspace_id, budgets["state"]),
        _build_code_hubs(conn, workspace_id, budgets["code_hubs"]),
        _build_associates(conn, workspace_id, budgets["associates"]),
        _build_recent_insights(conn, workspace_id, budgets["recent_insights"]),
        _build_watch_outs(conn, workspace_id, budgets["watch_outs"]),
    ]
    body_parts: list[str] = []
    for section in sections:
        body_parts.extend(section.lines)
    body_md = "\n".join(body_parts)
    # Hard cap: if heuristic budgets overshoot, trim from the tail (least-critical first).
    if approx_tokens(body_md) > max_tokens:
        # Re-fit by progressively dropping later sections.
        priority_order = [
            "identity",
            "behaviors",
            "decisions",
            "state",
            "code_hubs",
            "associates",
            "recent_insights",
            "watch_outs",
        ]
        trimmed_sections: list[BriefSection] = []
        running = 0
        for name in priority_order:
            sec = next(s for s in sections if s.name == name)
            cost = sum(approx_tokens(line) for line in sec.lines)
            if running + cost > max_tokens and trimmed_sections:
                continue
            trimmed_sections.append(sec)
            running += cost
        sections = trimmed_sections
        body_parts = []
        for section in sections:
            body_parts.extend(section.lines)
        body_md = "\n".join(body_parts)
    result = Brief(
        body_md=body_md,
        token_count=approx_tokens(body_md),
        sections=sections,
        cache_hit=False,
    )
    _cache_remember(cache_key, result)
    return result


# ============================================================
# Skill body fetch (used by memory_invoke_skill — separate from brief)
# ============================================================


def fetch_skill_body(
    conn: sqlite3.Connection, *, workspace_id: str, skill_id: str
) -> dict[str, Any] | None:
    """Return full skill row with body_md. Used by memory_invoke_skill only.

    Bumps usage_count + last_invoked_at as a side effect — the invoke
    counts as one use.
    """
    obj = get_object(
        conn,
        workspace_id=workspace_id,
        kind="skill",
        object_id=skill_id,
        fields=["body_md", "summary"],
    )
    if obj is None:
        return None
    conn.execute(
        "UPDATE skills SET usage_count = usage_count + 1, "
        "last_invoked_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
        "WHERE workspace_id = ? AND id = ?",
        (workspace_id, skill_id),
    )
    conn.commit()
    return obj
