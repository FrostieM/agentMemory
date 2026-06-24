"""Phase 4: load reflex_rules and evaluate against the session trail.

The lint module calls ``check_reflexes`` after its normal enforcement
dispatch. Each violation lifts the verdict from ``allow_with_advisories``
to ``block``, unless ``MEMORY_LINT_BLOCK_OVERRIDE=1`` is set.

Three precondition kinds ship in Phase 4 v1:

* ``impact_check_within_seconds``  -- agent must have called
  ``memory_impact_check`` on the same file within the window. (Phase 4
  v1: any prior call in the trail counts as evidence; the window check
  is a future enhancement once timestamps land in session_trail.)

* ``memory_search_within_seconds`` -- agent must have called
  ``memory_search`` recently. Used before architectural writes.

* ``playbook_fetch`` -- agent must have called
  ``memory_invoke_skill`` (subtype=playbook) before the dangerous tool
  (Bash deploy/publish, etc.).

Failure-soft: pre-migration DBs (no ``reflex_rules`` table) return zero
violations. Settings flag off → zero violations.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from agent_memory_lite.enforcement.reflex_check_payload_match import _payload_matches_pattern
from agent_memory_lite.enforcement.reflex_check_preconditions import (
    _advisory_text,
    _precondition_satisfied,
)


@dataclass(frozen=True, slots=True)
class ReflexViolation:
    """One reflex rule that fired for the current tool call."""

    rule_id: str
    rule_name: str
    trigger_tool: str
    precondition_kind: str
    enforcement: str
    advisory: str
    derived_from_insight_id: str | None = None


def _load_active_rules(
    conn: sqlite3.Connection, *, workspace_id: str, tool_name: str
) -> list[sqlite3.Row]:
    """Read active reflex rules for the given tool. Pre-migration-safe."""
    try:
        return conn.execute(
            """SELECT id, rule_name, trigger_tool, trigger_pattern,
                      precondition_kind, precondition_param_json,
                      enforcement, derived_from_insight_id
               FROM reflex_rules
               WHERE workspace_id = ? AND active = 1 AND trigger_tool = ?""",
            (workspace_id, tool_name),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def check_reflexes(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    tool_name: str,
    tool_payload: dict[str, object],
    trail: list[str],
    block_override: bool = False,
) -> list[ReflexViolation]:
    """Evaluate active reflex rules against the current tool call.

    Returns violations in rule-name order. ``block_override=True``
    rewrites all violations' ``enforcement`` to ``'advisory'`` so the
    verdict map (in lint.py) does not lift to ``block``.
    """
    rules = _load_active_rules(conn, workspace_id=workspace_id, tool_name=tool_name)
    if not rules:
        return []
    violations: list[ReflexViolation] = []
    for row in rules:
        pattern = row["trigger_pattern"] or ""
        if not _payload_matches_pattern(pattern, tool_payload):
            continue
        try:
            params = json.loads(row["precondition_param_json"] or "{}")
        except (TypeError, ValueError):
            params = {}
        if _precondition_satisfied(precondition_kind=row["precondition_kind"], trail=trail):
            continue
        enforcement = row["enforcement"] or "advisory"
        if block_override and enforcement == "block":
            enforcement = "advisory"
        violations.append(
            ReflexViolation(
                rule_id=row["id"],
                rule_name=row["rule_name"],
                trigger_tool=row["trigger_tool"],
                precondition_kind=row["precondition_kind"],
                enforcement=enforcement,
                advisory=_advisory_text(row["rule_name"], row["precondition_kind"], params),
                derived_from_insight_id=row["derived_from_insight_id"],
            )
        )
    return violations


def has_block_violation(violations: list[ReflexViolation]) -> bool:
    """Helper for the lint verdict mapper."""
    return any(v.enforcement == "block" for v in violations)


def record_fired(
    conn: sqlite3.Connection,
    *,
    violations: list[ReflexViolation],
    now_iso: str,
) -> None:
    """Increment block_count / advisory_count for fired rules. Failure-soft."""
    for v in violations:
        column = "block_count" if v.enforcement == "block" else "advisory_count"
        try:
            conn.execute(
                f"UPDATE reflex_rules SET {column} = {column} + 1, last_fired_at = ? WHERE id = ?",
                (now_iso, v.rule_id),
            )
        except sqlite3.OperationalError:
            continue
    conn.commit()
