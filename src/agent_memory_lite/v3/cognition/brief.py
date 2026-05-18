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

from agent_memory_lite.v3.storage.reader import (
    count_kind,
    get_object,
    list_kind,
)

DEFAULT_TOKEN_BUDGET = 500

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
    """Keep lines from the top until the budget is exhausted. Returns trimmed list."""
    out: list[str] = []
    used = 0
    for line in lines:
        cost = approx_tokens(line)
        if used + cost > budget:
            break
        out.append(line)
        used += cost
    return out


# ============================================================
# Section builders
# ============================================================


def _build_identity(conn: sqlite3.Connection, workspace_id: str, budget: int) -> BriefSection:
    """Identity layer: workspace name + counts overview + discipline reminder.

    The discipline reminder is the single most important line in the
    brief — it's how v3 closes the "agent knows about graph tools but
    still uses Read" gap. The line stays foreground in every session,
    so the agent's first instinct on a new file is impact_check, not
    Read.
    """
    lines = [f"# {workspace_id}"]
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
    rows = list_kind(conn, workspace_id=workspace_id, kind="decision", limit=limit * 3)
    # Prefer pinned + active first, then recency.
    rows.sort(
        key=lambda d: (
            0 if d.get("pinned") else 1,
            0 if d.get("status") == "active" else 1,
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
    fingerprint took ~2 minutes -- long enough that the v3 brief hook
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
    weights = {
        "identity": 0.20,
        "behaviors": 0.24,
        "decisions": 0.26,
        "state": 0.12,
        "code_hubs": 0.18,
    }
    budgets = {name: int(max_tokens * w) for name, w in weights.items()}

    sections = [
        _build_identity(conn, workspace_id, budgets["identity"]),
        _build_pinned_behaviors(conn, workspace_id, budgets["behaviors"]),
        _build_top_decisions(conn, workspace_id, budgets["decisions"]),
        _build_state(conn, workspace_id, budgets["state"]),
        _build_code_hubs(conn, workspace_id, budgets["code_hubs"]),
    ]
    body_parts: list[str] = []
    for section in sections:
        body_parts.extend(section.lines)
    body_md = "\n".join(body_parts)
    # Hard cap: if heuristic budgets overshoot, trim from the tail (least-critical first).
    if approx_tokens(body_md) > max_tokens:
        # Re-fit by progressively dropping later sections.
        priority_order = ["identity", "behaviors", "decisions", "state", "code_hubs"]
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
