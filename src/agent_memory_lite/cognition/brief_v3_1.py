"""Brief sections for v3.1 active-memory vectors.

Lifted out of ``brief.py`` to avoid growing that already-grandfathered
670-SLOC module further. Each builder follows the same contract as
``brief._build_blindspots``: takes (conn, workspace_id, budget) and
returns a ``BriefSection`` whose ``lines`` are pre-fitted to budget.

* ``_build_open_proposals`` — Vector 1: surface pending
  ``memory_candidate(kind='theory_proposal')`` rows in 'new' state so
  the operator sees what the heuristic proposed without leaving the
  brief.
* ``_build_predictive_warnings`` — Vector 5: surface decision pairs
  where a fresh decision overlaps a known low-outcome failure.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_memory_lite.cognition.brief import BriefSection


def _make_section(name: str, budget: int, lines: list[str]) -> BriefSection:
    """Local helper so each builder stays brief — defers BriefSection
    import to runtime to keep the TYPE_CHECKING fence clean."""
    from agent_memory_lite.cognition.brief import (  # noqa: PLC0415
        BriefSection,
        fit_to_budget,
    )

    return BriefSection(name=name, budget=budget, lines=fit_to_budget(lines, budget))


def _empty_section(name: str, budget: int) -> BriefSection:
    from agent_memory_lite.cognition.brief import BriefSection  # noqa: PLC0415

    return BriefSection(name=name, budget=budget, lines=[])


def _read_proposal_candidates(conn: sqlite3.Connection, workspace_id: str) -> list[sqlite3.Row]:
    """Read canonical pending theory-proposal candidates."""
    try:
        return conn.execute(
            "SELECT id, subject, object FROM candidates "
            "WHERE workspace_id = ? AND kind = 'theory_proposal' "
            "AND status = 'new' "
            "ORDER BY updated_at DESC LIMIT 5",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def build_open_proposals(conn: sqlite3.Connection, workspace_id: str, budget: int) -> BriefSection:
    """Pending theory-proposal candidates from Vector 1's scanner.

    Audit M5 fix: respects ``MEMORY_EXPERIMENT_PROPOSAL_ENABLED`` so the
    feature is byte-equivalent off-path (mirrors Vector 5).
    """
    if budget <= 0:
        return _empty_section("open_proposals", budget)
    try:
        from agent_memory_lite.maintenance.experiment_proposal import (  # noqa: PLC0415
            is_enabled,
        )

        if not is_enabled():
            return _empty_section("open_proposals", budget)
    except ImportError:  # pragma: no cover - defensive
        return _empty_section("open_proposals", budget)
    rows = _read_proposal_candidates(conn, workspace_id)
    if not rows:
        return _empty_section("open_proposals", budget)
    # Tight format mirrors _build_blindspots: short header + 1-line per row
    # so multiple proposals fit a 0.03 budget slice.
    lines: list[str] = ["## Open proposals (v3.1 Vector 1)"]
    for row in rows:
        if isinstance(row, sqlite3.Row):
            cid = str(row["id"])
            subject = str(row["subject"] or "")
        else:
            cid, subject = str(row[0]), str(row[1] or "")
        lines.append(f"- {cid}: from {subject}")
    return _make_section("open_proposals", budget, lines)


def build_predictive_warnings(
    conn: sqlite3.Connection, workspace_id: str, budget: int
) -> BriefSection:
    """Predictive failure warnings from Vector 5's scanner.

    Calls ``find_predictive_warnings`` directly so the brief is
    consistent with what the MCP tool / HTTP route return. Failure-soft:
    on schema-missing (``OperationalError``) returns an empty section.
    """
    if budget <= 0:
        return _empty_section("predictive_warnings", budget)
    try:
        from agent_memory_lite.maintenance.predictive_failure import (  # noqa: PLC0415
            find_predictive_warnings,
            is_enabled,
        )

        if not is_enabled():
            return _empty_section("predictive_warnings", budget)
        warnings = find_predictive_warnings(conn, workspace_id=workspace_id, limit=3)
    except (sqlite3.OperationalError, ImportError):
        return _empty_section("predictive_warnings", budget)
    if not warnings:
        return _empty_section("predictive_warnings", budget)
    lines: list[str] = ["## Predictive warnings (v3.1 Vector 5)"]
    for w in warnings:
        lines.append(
            f"- {w.fresh_decision_id} ~ {w.failed_decision_id} (jaccard={w.similarity:.2f})"
        )
    return _make_section("predictive_warnings", budget, lines)
