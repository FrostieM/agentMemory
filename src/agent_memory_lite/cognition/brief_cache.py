"""Brief cache + workspace fingerprint.

The in-process LRU brief cache and the fingerprint logic that keys it.
Extracted from cognition/brief.py during the v3.7 SLOC decomposition.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading

from agent_memory_lite.cognition.brief_models import Brief

# In-process brief cache, keyed on (workspace_id, max_tokens, fingerprint).
# Fingerprint is a hash of cardinal "last write" timestamps for the kinds
# that contribute to the brief — invalidates automatically on any
# mutation in those tables. Bounded to 16 entries; eviction is LRU via
# dict insertion order.
_BRIEF_CACHE_MAX = 16
_BRIEF_CACHE: dict[tuple[str, int, str], Brief] = {}
# Round-2 audit: LRU eviction uses `next(iter(...))` to find the oldest
# entry, then `del`. Two threads in the eviction loop together race on
# both the iteration and the del — dict reordering can raise
# RuntimeError. Lock turns get/promote/insert/evict into one atomic
# critical section per access.
_BRIEF_CACHE_LOCK = threading.Lock()

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


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    """True when ``name`` exists as a table. Lets the fingerprint query
    skip optional tables that a pre-migration database may not have."""
    try:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
            is not None
        )
    except sqlite3.Error:
        return False


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """True when ``table.column`` exists. Lets the fingerprint fold in an
    optional column (e.g. ``outcome_score``, added by migration 0002)
    without raising -- and bypassing the cache entirely -- on a
    pre-migration DB that has the table but not the column."""
    try:
        return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))
    except sqlite3.Error:
        return False


def _workspace_fingerprint(conn: sqlite3.Connection, workspace_id: str) -> str:
    """Hash the per-table mutation timestamps that affect the brief.

    Reads ``MAX(updated_at)`` from decisions / behaviors / theories /
    tasks / code_digests / capability_links / plan_steps / insights /
    concepts / skills / issues and ``MAX(created_at)`` from episodes for
    the workspace, then hashes the per-table maxima together. A write to any of those tables moves that
    table's own maximum, so the fingerprint flips → cache miss on next
    call. It also folds in a per-table ``outcome_score`` aggregate for
    decisions / behaviors / theories / insights, because outcome recompute
    rewrites that column without touching ``updated_at`` (see the inline
    note below).

    Per-table, not a single global ``MAX``: a global max hides a write
    to one table behind a higher timestamp in another -- including the
    mixed ``Z`` / ``+00:00`` timestamp formats across writers, where
    ``+00:00`` sorts below ``Z`` within the same second. Hashing each
    table's own max removes that cross-table / cross-format masking. A
    residual gap remains: a write that does not advance its table's
    ``MAX`` -- a repeat write inside the same wall-clock second, or a
    back-dated row -- is not detected. That is irreducible at second
    timestamp granularity and affects every table equally.

    Returns the first 12 chars of SHA1 (collision-safe at this cache
    size). On any SQL error returns a unique sentinel so the call
    bypasses the cache instead of returning stale content.

    The maxima come from independent scalar sub-queries, each a small
    index-seek scan -- never chained ``LEFT JOIN``s, which a 2026-05-18
    bug fix found became a Cartesian product that took ~2 minutes on a
    busy workspace.
    """
    # Optional tables -- added only when present. ``plan_steps`` may be
    # absent on a partial-schema DB.
    # capability_links + plan_steps feed the brief's active-plan section
    # (Phase 3/4); the rest feed the other sections.
    # Optional tables -- folded in only when present (several arrive in later
    # migrations). Each feeds a brief surface: plan_steps/candidates -> active
    # plan; insights -> recent_insights/lessons; concepts/skills -> associates
    # (their active=0 terminal state); issues -> open_issues + associates. A
    # status/active flip on any bumps updated_at (all six carry it), so the
    # table's MAX(updated_at) invalidates the cached brief on a discredit.
    # NOTE: episode/chunk also surface (associates) and have an is_archived
    # terminal state, but those tables carry NO updated_at, so the fingerprint
    # cannot detect an is_archived flip there. That residual is bounded: the
    # compose-time _is_discredited filter drops them on every cache miss, and
    # the cache self-heals on the next write to any tracked table.
    optional: list[str] = [
        t
        for t in ("candidates", "plan_steps", "insights", "concepts", "skills", "issues")
        if _has_table(conn, t)
    ]
    # One scalar sub-query per table -- episodes keys on created_at, the
    # rest on updated_at; each binds the same workspace_id.
    subqueries = [
        "(SELECT MAX(updated_at) FROM decisions WHERE workspace_id = ?)",
        "(SELECT MAX(updated_at) FROM behaviors WHERE workspace_id = ?)",
        # theories feed the brief as associates (_is_discredited) AND watch-outs;
        # a status flip (rejected/weakened/superseded) bumps updated_at, so the
        # table must be in the fingerprint or that flip serves a stale brief.
        "(SELECT MAX(updated_at) FROM theories WHERE workspace_id = ?)",
        "(SELECT MAX(updated_at) FROM tasks WHERE workspace_id = ?)",
        "(SELECT MAX(updated_at) FROM code_digests WHERE workspace_id = ?)",
        "(SELECT MAX(created_at) FROM episodes WHERE workspace_id = ?)",
        "(SELECT MAX(updated_at) FROM capability_links WHERE workspace_id = ?)",
    ]
    subqueries += [f"(SELECT MAX(updated_at) FROM {t} WHERE workspace_id = ?)" for t in optional]
    # Outcome-score sensitivity (round-2 audit). outcome_recompute.refresh_one /
    # refresh_workspace write ``outcome_score`` WITHOUT advancing ``updated_at``,
    # so an outcome-only discredit (the brain pass pins an archived/superseded
    # row, or feedback drives one negative) does NOT move MAX(updated_at) -- and
    # the brief's seed filter + _is_discredited neighbor filter gate on
    # outcome_score, so a stale cached brief would keep surfacing a now-negative
    # row as a positive associate. Fold a per-table outcome aggregate in so any
    # score change flips the fingerprint. TOTAL() (never NULL) catches single-row
    # changes; MIN/MAX make a multi-row sum-cancellation effectively impossible.
    # decisions/behaviors/theories/insights all carry outcome_score and surface
    # on outcome-gated brief sections; _has_column keeps each pre-migration-safe.
    for tbl in ("decisions", "behaviors", "theories", "insights"):
        if _has_column(conn, tbl, "outcome_score"):
            subqueries.append(
                f"(SELECT TOTAL(outcome_score) || '/' || IFNULL(MIN(outcome_score), '') "
                f"|| '/' || IFNULL(MAX(outcome_score), '') FROM {tbl} WHERE workspace_id = ?)"
            )
    params: list[str] = [workspace_id] * len(subqueries)
    try:
        row = conn.execute(f"SELECT {', '.join(subqueries)}", tuple(params)).fetchone()
    except sqlite3.Error:
        return f"err-{workspace_id}-{id(conn)}"
    if row is None:
        return f"empty-{workspace_id}"
    raw = "|".join(str(value or "") for value in row)
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def _cache_remember(key: tuple[str, int, str], brief: Brief) -> None:
    """LRU-style insert into the bounded brief cache. Lock-protected:
    pop / set / evict together so concurrent inserts can't race on
    eviction iteration (Python's dict raises RuntimeError when its
    insertion order mutates mid-iteration)."""
    with _BRIEF_CACHE_LOCK:
        _BRIEF_CACHE.pop(key, None)
        _BRIEF_CACHE[key] = brief
        while len(_BRIEF_CACHE) > _BRIEF_CACHE_MAX:
            # Pop oldest (insertion-order = LRU because we del+set on hit).
            oldest = next(iter(_BRIEF_CACHE))
            del _BRIEF_CACHE[oldest]
