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
import re
import sqlite3
from dataclasses import dataclass

from agent_memory_lite.enforcement.session_trail import has_called

# Precondition kind → tools that satisfy it.
_PRECONDITION_TOOL_MAP: dict[str, tuple[str, ...]] = {
    "impact_check_within_seconds": ("memory_impact_check",),
    "memory_search_within_seconds": ("memory_search", "memory_get_context"),
    "playbook_fetch": ("memory_invoke_skill",),
}


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


_MAX_PATTERN_LEN = 256
_MAX_CANDIDATE_LEN = 4096


def _payload_matches_pattern(pattern: str, tool_payload: dict[str, object]) -> bool:
    """Empty pattern matches anything; otherwise regex-search common string fields.

    The payload shape varies wildly per tool, so we test the regex against
    each candidate field independently rather than concatenating them
    (which would break end-of-string anchors like ``\\.py$``).

    ReDoS defense:
    - patterns longer than ``_MAX_PATTERN_LEN`` are rejected outright
      (typical reflex rule pattern is < 40 chars; 256 is generous)
    - candidate strings are truncated to ``_MAX_CANDIDATE_LEN`` so an
      attacker-controlled command line cannot blow up backtracking
    - invalid regex silently fails (rule doesn't fire) rather than
      raising into the PreToolUse hook
    """
    if not pattern:
        return True
    if len(pattern) > _MAX_PATTERN_LEN:
        return False
    candidates: list[str] = []
    for key in ("file_path", "pattern", "command", "query", "path", "name"):
        value = tool_payload.get(key)
        if value is None:
            continue
        as_str = str(value).strip()
        if as_str:
            candidates.append(as_str[:_MAX_CANDIDATE_LEN])
    if not candidates:
        return False
    try:
        compiled = re.compile(pattern)
    except re.error:
        return False
    return any(compiled.search(c) for c in candidates)


def _precondition_satisfied(*, precondition_kind: str, trail: list[str]) -> bool:
    """True if the trail contains evidence that the precondition was met."""
    tools = _PRECONDITION_TOOL_MAP.get(precondition_kind)
    if not tools:
        # Unknown kind -- safest default is to skip the check entirely
        # so a typo in operator-seeded rule doesn't lock the agent out.
        return True
    return has_called(trail, *tools)


def _advisory_text(rule_name: str, precondition_kind: str, params: dict[str, object]) -> str:
    """Human-readable failure message for the diagnostic line."""
    window = params.get("window_seconds")
    window_clause = f" within {window}s" if window else ""
    fallback_tool = ", ".join(_PRECONDITION_TOOL_MAP.get(precondition_kind, ("(unknown)",)))
    return (
        f"reflex {rule_name!r}: precondition {precondition_kind!r}"
        f"{window_clause} not satisfied. Call {fallback_tool} first, "
        f"then retry this tool."
    )


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
