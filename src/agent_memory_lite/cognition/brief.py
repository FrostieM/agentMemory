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

Sticky-brief: when the caller passes ``session_id``, the FIRST brief for
that (workspace, session) pair renders at the requested budget. Every
subsequent emit in the same session shrinks the cap to
``MEMORY_STICKY_BRIEF_FOLLOWUP_TOKENS`` (default 200) so a long chat
session doesn't keep paying the full ~500-token tax on every prompt.
Set ``MEMORY_STICKY_BRIEF_ENABLED=false`` to disable.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from agent_memory_lite.cognition.brief_v3_1 import (
    build_open_proposals as _build_open_proposals,
)
from agent_memory_lite.cognition.brief_v3_1 import (
    build_predictive_warnings as _build_predictive_warnings,
)
from agent_memory_lite.storage.reader import (
    count_kind,
    get_object,
    list_kind,
)

DEFAULT_TOKEN_BUDGET = 500

# Sticky-brief: per-process record of which (workspace, session) pairs
# have already received a full brief. Used to decide whether to cap
# ``max_tokens`` on subsequent emits within the same session.
_session_seen: set[tuple[str, str]] = set()


def _sticky_brief_enabled() -> bool:
    raw = os.environ.get("MEMORY_STICKY_BRIEF_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _sticky_followup_tokens(default: int = 200) -> int:
    raw = os.environ.get("MEMORY_STICKY_BRIEF_FOLLOWUP_TOKENS", str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(100, value)  # floor matches the HTTP brief endpoint min


def reset_session_seen() -> None:
    """Test hook: clear the sticky-brief seen-sessions set."""
    _session_seen.clear()


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
# Section assembly + workspace-aware budget redistribution
# ============================================================

# Sections that receive freed budget when other sections come back empty.
# Identity stays first (discipline reminder must render); behaviors /
# decisions / aging_decisions absorb the rest in priority order.
_REDISTRIBUTE_RECIPIENTS: tuple[str, ...] = (
    "identity",
    "behaviors",
    "decisions",
    "aging_decisions",
)


def _build_all_sections(
    conn: sqlite3.Connection, workspace_id: str, budgets: dict[str, int]
) -> list[BriefSection]:
    """Single-pass build with the supplied per-name budgets."""
    return [
        _build_identity(conn, workspace_id, budgets["identity"]),
        _build_pinned_behaviors(conn, workspace_id, budgets["behaviors"]),
        _build_top_decisions(conn, workspace_id, budgets["decisions"]),
        _build_state(conn, workspace_id, budgets["state"]),
        _build_code_hubs(conn, workspace_id, budgets["code_hubs"]),
        _build_associates(conn, workspace_id, budgets["associates"]),
        _build_recent_insights(conn, workspace_id, budgets["recent_insights"]),
        _build_watch_outs(conn, workspace_id, budgets["watch_outs"]),
        _build_aging_decisions(conn, workspace_id, budgets["aging_decisions"]),
        _build_blindspots(conn, workspace_id, budgets["blindspots"]),
        # v3.1 active-memory vectors — surface heuristic outputs so the
        # agent sees them at session start without an explicit MCP call.
        _build_open_proposals(conn, workspace_id, budgets["open_proposals"]),
        _build_predictive_warnings(conn, workspace_id, budgets["predictive_warnings"]),
    ]


def _redistribute_and_rebuild(
    conn: sqlite3.Connection,
    workspace_id: str,
    budgets: dict[str, int],
    sections: list[BriefSection],
    empty_names: set[str],
) -> list[BriefSection]:
    """Pull budget from sections that returned empty + rebuild the
    priority recipients with the extra room.

    Only rebuilds the recipients (identity / behaviors / decisions /
    aging_decisions) so the second pass costs at most 4 SQL queries.
    """
    freed = sum(budgets[name] for name in empty_names)
    if freed <= 0:
        return sections
    new_budgets = dict(budgets)
    for name in empty_names:
        new_budgets[name] = 0
    # Proportional split based on initial recipient budgets so the
    # densest section gets the biggest slice.
    recipient_total = sum(budgets[name] for name in _REDISTRIBUTE_RECIPIENTS)
    if recipient_total <= 0:
        return sections
    by_name = {s.name: s for s in sections}
    for name in _REDISTRIBUTE_RECIPIENTS:
        share = int(freed * budgets[name] / recipient_total)
        new_budgets[name] = budgets[name] + share
    # Rebuild only the recipients; leave other sections as-is.
    by_name["identity"] = _build_identity(conn, workspace_id, new_budgets["identity"])
    by_name["behaviors"] = _build_pinned_behaviors(conn, workspace_id, new_budgets["behaviors"])
    by_name["decisions"] = _build_top_decisions(conn, workspace_id, new_budgets["decisions"])
    by_name["aging_decisions"] = _build_aging_decisions(
        conn, workspace_id, new_budgets["aging_decisions"]
    )
    return [by_name[s.name] for s in sections]


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


def _build_blindspots(conn: sqlite3.Connection, workspace_id: str, budget: int) -> BriefSection:
    """Surface tokens present in ≥N episodes but in ZERO active decisions.

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
        rows = find_blindspots(conn, workspace_id=workspace_id)
    except Exception:  # pragma: no cover - defensive
        return BriefSection(name="blindspots", budget=budget, lines=[])
    if not rows:
        return BriefSection(name="blindspots", budget=budget, lines=[])
    lines = ["## Blindspots (discussed but no decision)"]
    for row in rows:
        # Live-validation-2026-05-20: surface the LLM-augmented
        # description when v3.1 Vector 3 LLM is enabled. Trimmed to
        # ~120 chars so the section stays inside its budget — the
        # full description remains accessible via the underlying
        # ``find_blindspots`` API for callers that have more room.
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
    """Workspace-aware (P2): emit nothing on a workspace with no active
    tasks. The ``- no active tasks`` placeholder was useful onboarding
    text on fresh workspaces but noise after the first session. The
    freed budget is actually reallocated by ``_redistribute_and_rebuild``
    in ``compose_brief``: when this section returns empty, denser
    sections (identity / behaviors / decisions / aging_decisions) get a
    proportional bonus + re-render with bigger caps.
    """
    rows = list_kind(conn, workspace_id=workspace_id, kind="task", limit=3)
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
    return BriefSection(name="state", budget=budget, lines=fit_to_budget(lines, budget))


def _build_code_hubs(
    conn: sqlite3.Connection, workspace_id: str, budget: int, limit: int = 10
) -> BriefSection:
    """Workspace-aware (P2): emit nothing when the code-memory substrate
    is empty. Saves ~10 tokens on workspaces that don't use code-memory
    (non-software projects); the freed budget is rerouted by
    ``_redistribute_and_rebuild`` into the dense priority sections."""
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
    if not rows:
        return BriefSection(name="code_hubs", budget=budget, lines=[])
    lines = ["## Code hubs"]
    for row in rows:
        path = row["file_path"]
        purpose = row["purpose_short"] or "(no digest)"
        callers = row["inbound_edge_count"]
        lines.append(f"- {path}: {purpose} ({callers} callers)")
    return BriefSection(name="code_hubs", budget=budget, lines=fit_to_budget(lines, budget))


# ============================================================
# Top-level composer
# ============================================================


# Env vars whose state contributes to brief composition (which sections
# render, with what thresholds). Folded into the cache fingerprint so
# flipping them does not require a process restart (audit 3 #2 fix).
_BRIEF_ENV_KEYS: tuple[str, ...] = (
    "MEMORY_AGING_DECISIONS_ENABLED",
    "MEMORY_AGING_DECISIONS_DAYS",
    "MEMORY_AGING_DECISIONS_LIMIT",
    "MEMORY_BLINDSPOT_DETECT_ENABLED",
    "MEMORY_BLINDSPOT_LOOKBACK_DAYS",
    "MEMORY_BLINDSPOT_MIN_EPISODES",
    "MEMORY_BLINDSPOT_LIMIT",
    "MEMORY_STICKY_BRIEF_ENABLED",
    "MEMORY_STICKY_BRIEF_FOLLOWUP_TOKENS",
    # v3.1 brief integration: open_proposals + predictive_warnings
    # sections respond to these flags. Without them in the fingerprint,
    # toggling the flag mid-session would silently render a stale brief.
    "MEMORY_EXPERIMENT_PROPOSAL_ENABLED",
    "MEMORY_EXPERIMENT_PROPOSAL_CONF_MIN",
    "MEMORY_EXPERIMENT_PROPOSAL_CONF_MAX",
    "MEMORY_EXPERIMENT_PROPOSAL_LLM_ENABLED",
    "MEMORY_PREDICTIVE_FAILURE_ENABLED",
    "MEMORY_PREDICTIVE_FAILURE_OUTCOME_THRESHOLD",
    "MEMORY_PREDICTIVE_FAILURE_JACCARD_MIN",
)


def _brief_env_signature() -> str:
    """Stable hash of env vars that affect brief composition.

    Read each var fresh (so live changes invalidate the cache on the
    next call). Output goes into the cache key alongside the workspace
    fingerprint — toggling a flag is enough to evict the cached body.

    12-hex chars matches the workspace-fingerprint width (audit-round-2
    B#3) — visual symmetry + bigger collision margin than the original
    8-hex.
    """
    parts = [f"{k}={os.environ.get(k, '')}" for k in _BRIEF_ENV_KEYS]
    return hashlib.sha1("|".join(parts).encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


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
    # Audit-5 H2 fix: include the candidates table (legacy
    # ``memory_candidates`` OR canonical ``candidates``) so a Vector 1
    # ``persist=true`` write invalidates the cached brief immediately.
    # Detect which table is present so canonical-only deploys (where
    # the legacy name is absent) don't fail the whole query.
    cand_table = ""
    try:
        for name in ("memory_candidates", "candidates"):
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
            if row:
                cand_table = name
                break
    except sqlite3.Error:
        cand_table = ""
    extra_clause = (
        f"  UNION ALL SELECT updated_at FROM {cand_table} WHERE workspace_id = ?"
        if cand_table
        else ""
    )
    params: list[str] = [
        workspace_id,
        workspace_id,
        workspace_id,
        workspace_id,
        workspace_id,
    ]
    if cand_table:
        params.append(workspace_id)
    try:
        rows = conn.execute(
            f"""
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
            {extra_clause}
            )
            """,
            tuple(params),
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
    session_id: str | None = None,
) -> Brief:
    """Build the brief for a workspace at session start.

    ``task`` is an optional natural-language task description; reserved
    for future task-biased section selection (e.g. rank decisions by
    overlap with task tokens). v1 ignores it and returns the static
    brief.

    ``session_id`` (optional) enables the sticky-brief tax cut. When
    supplied, the FIRST call for (workspace_id, session_id) renders at
    the requested ``max_tokens``; subsequent calls in the same session
    shrink the cap to ``MEMORY_STICKY_BRIEF_FOLLOWUP_TOKENS`` (default
    200). Disabled by ``MEMORY_STICKY_BRIEF_ENABLED=false``. Callers
    that pass no ``session_id`` (legacy) keep full-budget behavior.

    Cached on (workspace_id, effective_max_tokens, workspace_fingerprint).
    Cache hits return ``Brief(cache_hit=True)`` in ~sub-millisecond. The
    fingerprint flips on any decision / behavior / task / code_digest /
    episode mutation, so a stale cache cannot survive a write.
    """
    del task  # reserved; future task-biased ranking
    effective_max = max_tokens
    if session_id and _sticky_brief_enabled():
        session_key = (workspace_id, session_id)
        if session_key in _session_seen:
            # Follow-up call in the same session — shrink the cap.
            effective_max = min(max_tokens, _sticky_followup_tokens())
        else:
            _session_seen.add(session_key)
    fingerprint = _workspace_fingerprint(conn, workspace_id) + ":" + _brief_env_signature()
    cache_key = (workspace_id, effective_max, fingerprint)
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
    # Audit-driven rebalance 2026-05-19 + v3.1 blindspot slot:
    # aging-decisions was 0.02 → 0.04 so the section actually fits.
    # Added 0.03 for ``blindspots`` (v3.1 Vector 3 — structural
    # episode-vs-decision asymmetry). Trimmed ``identity`` 0.18 → 0.15
    # to absorb the new slot while keeping the sum at exactly 1.00.
    # v3.1 brief integration: open_proposals + predictive_warnings each
    # take 0.03 of the budget. Trimmed identity (-0.02), decisions
    # (-0.02), state (-0.02) to absorb 0.06 while keeping sum at 1.00.
    weights = {
        "identity": 0.13,
        "behaviors": 0.20,
        "decisions": 0.20,
        "state": 0.10,
        "code_hubs": 0.10,
        "associates": 0.05,
        "recent_insights": 0.04,
        "watch_outs": 0.05,
        "aging_decisions": 0.04,
        "blindspots": 0.03,
        "open_proposals": 0.03,
        "predictive_warnings": 0.03,
    }
    budgets = {name: int(effective_max * w) for name, w in weights.items()}

    sections = _build_all_sections(conn, workspace_id, budgets)
    # Audit-round-P2 #1 fix: workspace-aware redistribution. Sections
    # that returned empty (no lines) on the first pass free their
    # budget to the dense priority sections (identity / behaviors /
    # decisions / aging_decisions). On a fresh workspace with no tasks
    # / no code_digests this gives the active content meaningful room.
    empty_names = {s.name for s in sections if not s.lines}
    if empty_names:
        sections = _redistribute_and_rebuild(conn, workspace_id, budgets, sections, empty_names)
    body_parts: list[str] = []
    for section in sections:
        body_parts.extend(section.lines)
    body_md = "\n".join(body_parts)
    # Hard cap: if heuristic budgets overshoot, trim from the tail (least-critical first).
    if approx_tokens(body_md) > effective_max:
        # Re-fit by progressively dropping later sections.
        # v3.1 audit-5 H4 fix: include the new sections so an overshoot
        # doesn't silently drop them — exactly the moment the operator
        # most needs to see Vector 1/5 warnings.
        priority_order = [
            "identity",
            "behaviors",
            "decisions",
            "state",
            "code_hubs",
            "associates",
            "recent_insights",
            "watch_outs",
            "aging_decisions",
            "blindspots",
            "open_proposals",
            "predictive_warnings",
        ]
        trimmed_sections: list[BriefSection] = []
        running = 0
        for name in priority_order:
            sec = next(s for s in sections if s.name == name)
            cost = sum(approx_tokens(line) for line in sec.lines)
            if running + cost > effective_max and trimmed_sections:
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
