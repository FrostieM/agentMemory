"""Brief section assembly + workspace-aware budget redistribution.

Builds every section in one pass, then — when sections come back empty
— pulls their freed budget into the dense priority sections and rebuilds
just those. Extracted from cognition/brief.py during the v3.7 SLOC
decomposition.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.cognition.brief_behaviors import _build_pinned_behaviors
from agent_memory_lite.cognition.brief_models import BriefSection
from agent_memory_lite.cognition.brief_sections_core import (
    _build_code_hubs,
    _build_identity,
    _build_state,
    _build_top_decisions,
)
from agent_memory_lite.cognition.brief_sections_organ import (
    _build_associates,
    _build_recent_insights,
)
from agent_memory_lite.cognition.brief_sections_plan import _build_active_plan
from agent_memory_lite.cognition.brief_sections_watch import (
    _build_aging_decisions,
    _build_blindspots,
    _build_watch_outs,
)
from agent_memory_lite.cognition.brief_v3_1 import (
    build_open_proposals as _build_open_proposals,
)
from agent_memory_lite.cognition.brief_v3_1 import (
    build_predictive_warnings as _build_predictive_warnings,
)

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
        _build_active_plan(conn, workspace_id, budgets["active_plan"]),
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
