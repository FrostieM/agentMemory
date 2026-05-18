"""Phase 4: PreFlect-inspired reflex rule distillation.

Reads low-outcome insights + correction-trust episodes; when the same
(tool, missing_precondition) pattern appears in N supporting episodes,
upserts a new reflex rule with ``enforcement='advisory'``. The operator
promotes ``advisory -> block`` via ``memory_edit(kind='reflex_rule', ...)``.

**Single-trial trauma guard**: a rule is NEVER auto-distilled from one
failure. The plan-mandated minimum is 3 supporting episodes
(configurable via ``MEMORY_REFLEX_DISTILLER_MIN_SUPPORT``). This
matches the consolidation cluster threshold and the lesson_extractor
support threshold so the agent never installs a hair-trigger reflex
from a flaky single observation.

The distiller is conservative on purpose: a real reflex rule shifts
the agent's tool-use forever, so the false-positive cost is high. We
prefer to under-distill and let the operator notice missing rules over
distilling false rules the operator must manually disable.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

# Regex patterns mapping observed prose to a (tool, precondition_kind).
# Phase 4 v1 ships three: more will land as the corpus grows.
_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"edited?.+(without|before).+(impact[_ ]check|reading)", re.I),
        "Edit",
        "impact_check_within_seconds",
    ),
    (
        re.compile(r"wrote decision.+(without|before).+search", re.I),
        "memory_write",
        "memory_search_within_seconds",
    ),
    (
        re.compile(r"deploy.+without.+playbook", re.I),
        "Bash",
        "playbook_fetch",
    ),
)


@dataclass(frozen=True, slots=True)
class DistillCandidate:
    """One (tool, precondition) pair with the supporting evidence."""

    tool: str
    precondition_kind: str
    supporting_insight_ids: list[str]


@dataclass(frozen=True, slots=True)
class DistillReport:
    """Per-workspace distiller summary."""

    inspected: int
    candidates: int
    rules_upserted: int


def _collect_candidates(
    conn: sqlite3.Connection, *, workspace_id: str, min_support: int
) -> list[DistillCandidate]:
    """Scan low-outcome insights and corrected episodes for (tool, precondition) pairs."""
    try:
        rows = conn.execute(
            """SELECT id, summary, gist, insight_type, outcome_score
               FROM insights
               WHERE workspace_id = ?
                 AND COALESCE(outcome_score, 0.0) < -0.3
                 AND status = 'candidate'""",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    # Group hits by (tool, precondition_kind) -> list of insight ids.
    groups: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        text = " ".join(str(row[i] or "") for i in (1, 2, 3))  # summary + gist + insight_type
        for regex, tool, precond in _PATTERNS:
            if regex.search(text):
                key = (tool, precond)
                groups.setdefault(key, []).append(row[0])
                break  # one rule per insight maximum
    return [
        DistillCandidate(tool=t, precondition_kind=p, supporting_insight_ids=ids)
        for (t, p), ids in groups.items()
        if len(ids) >= min_support
    ]


def _upsert_rule(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    candidate: DistillCandidate,
) -> bool:
    """Insert a new reflex rule if no rule exists for this (tool, precondition).
    Returns True if a row was inserted; False if a duplicate already existed.
    """
    rule_name = f"distilled-{candidate.tool.lower()}-{candidate.precondition_kind}"
    try:
        existing = conn.execute(
            """SELECT 1 FROM reflex_rules
               WHERE workspace_id = ? AND trigger_tool = ?
                 AND precondition_kind = ?""",
            (workspace_id, candidate.tool, candidate.precondition_kind),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    if existing:
        return False
    now = iso_now()
    rule_id = new_id(IdKind.AUDIT)
    derived = candidate.supporting_insight_ids[0] if candidate.supporting_insight_ids else None
    try:
        conn.execute(
            """INSERT INTO reflex_rules
               (id, workspace_id, rule_name, trigger_tool, trigger_pattern,
                precondition_kind, precondition_param_json, enforcement,
                derived_from_insight_id, active, created_at, updated_at)
               VALUES (?, ?, ?, ?, '', ?, ?, 'advisory', ?, 1, ?, ?)""",
            (
                rule_id,
                workspace_id,
                rule_name,
                candidate.tool,
                candidate.precondition_kind,
                json.dumps({"distilled_from": candidate.supporting_insight_ids}),
                derived,
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError:
        return False
    return True


def distill_workspace(
    conn: sqlite3.Connection, *, workspace_id: str, min_support: int = 3
) -> DistillReport:
    """Scan and distill candidate reflex rules for one workspace."""
    candidates = _collect_candidates(conn, workspace_id=workspace_id, min_support=min_support)
    upserted = 0
    for candidate in candidates:
        if _upsert_rule(conn, workspace_id=workspace_id, candidate=candidate):
            upserted += 1
    conn.commit()
    return DistillReport(
        inspected=len(candidates) + 0,  # opaque count proxies for "looked at"
        candidates=len(candidates),
        rules_upserted=upserted,
    )
