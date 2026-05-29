"""Top-level brief composer — budget allocation, cache, sticky tax.

Extracted from cognition/brief.py during the v3.7 SLOC decomposition.
``compose_brief`` is the public entry point; the brief.py facade
re-exports it so existing import sites are unaffected.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.cognition.brief_assembly import (
    _build_all_sections,
    _redistribute_and_rebuild,
)
from agent_memory_lite.cognition.brief_cache import (
    _BRIEF_CACHE,
    _BRIEF_CACHE_LOCK,
    _brief_env_signature,
    _cache_remember,
    _workspace_fingerprint,
)
from agent_memory_lite.cognition.brief_models import Brief, BriefSection
from agent_memory_lite.cognition.brief_sticky import (
    _SESSION_SEEN_LOCK,
    _session_seen,
    _sticky_brief_enabled,
    _sticky_followup_tokens,
)
from agent_memory_lite.cognition.brief_tokens import approx_tokens

DEFAULT_TOKEN_BUDGET = 500


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
    for future task-biased section selection. v1 ignores it.

    ``session_id`` (optional) enables the sticky-brief tax cut: the FIRST
    call for (workspace_id, session_id) renders at ``max_tokens``;
    subsequent calls shrink the cap to
    ``MEMORY_STICKY_BRIEF_FOLLOWUP_TOKENS`` (default 200). Disabled by
    ``MEMORY_STICKY_BRIEF_ENABLED=false``.

    Cached on (workspace_id, effective_max_tokens, workspace_fingerprint).
    The fingerprint flips on any decision / behavior / task / code_digest
    / episode mutation, so a stale cache cannot survive a write.
    """
    del task  # reserved; future task-biased ranking
    effective_max = max_tokens
    if session_id and _sticky_brief_enabled():
        session_key = (workspace_id, session_id)
        # Atomically check-and-add under the lock so two concurrent
        # requests for the same session don't both miss the membership
        # check and both render at full budget.
        with _SESSION_SEEN_LOCK:
            already_seen = session_key in _session_seen
            if not already_seen:
                _session_seen.add(session_key)
        if already_seen:
            # Follow-up call in the same session — shrink the cap.
            effective_max = min(max_tokens, _sticky_followup_tokens())
    fingerprint = _workspace_fingerprint(conn, workspace_id) + ":" + _brief_env_signature()
    cache_key = (workspace_id, effective_max, fingerprint)
    # GET + LRU-promote must run together under the lock; otherwise a
    # racing _cache_remember can shuffle insertion order between our
    # ``cached = ... get`` and our ``del + reinsert`` lines.
    with _BRIEF_CACHE_LOCK:
        cached = _BRIEF_CACHE.get(cache_key)
        if cached is not None:
            # Refresh LRU position so frequently-used briefs stay hot.
            del _BRIEF_CACHE[cache_key]
            _BRIEF_CACHE[cache_key] = cached
    if cached is not None:
        return Brief(
            body_md=cached.body_md,
            token_count=cached.token_count,
            sections=cached.sections,
            cache_hit=True,
        )
    # Per-section budget allocation (target ratios — sum to 1.00).
    weights = {
        "identity": 0.13,
        "behaviors": 0.20,
        "decisions": 0.20,
        "active_plan": 0.07,
        "state": 0.05,
        "code_hubs": 0.06,
        "associates": 0.05,
        "recent_insights": 0.03,
        "watch_outs": 0.05,
        "open_issues": 0.03,
        "aging_decisions": 0.04,
        "blindspots": 0.03,
        "open_proposals": 0.03,
        "predictive_warnings": 0.03,
    }
    budgets = {name: int(effective_max * w) for name, w in weights.items()}

    sections = _build_all_sections(conn, workspace_id, budgets)
    # Workspace-aware redistribution: sections that returned empty (no
    # lines) free their budget to the dense priority sections.
    empty_names = {s.name for s in sections if not s.lines}
    if empty_names:
        sections = _redistribute_and_rebuild(conn, workspace_id, budgets, sections, empty_names)
    body_parts: list[str] = []
    for section in sections:
        body_parts.extend(section.lines)
    body_md = "\n".join(body_parts)
    # Hard cap: if heuristic budgets overshoot, trim from the tail
    # (least-critical first) but always keep the v3.1 warning sections.
    if approx_tokens(body_md) > effective_max:
        priority_order = [
            "identity",
            "behaviors",
            "decisions",
            "active_plan",
            "state",
            "code_hubs",
            "associates",
            "recent_insights",
            "watch_outs",
            "open_issues",
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
