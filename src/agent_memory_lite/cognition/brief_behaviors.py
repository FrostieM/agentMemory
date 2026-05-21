"""Brief section builder for pinned behaviors — the safety-critical layer.

Extracted from cognition/brief.py during the v3.7 SLOC decomposition.
Reads pinned behaviors from BOTH the legacy ``behaviors`` table and the
canonical ``behavior_instructions`` table, dedupes, and renders them
priority-weighted so safety rules can't be pushed out by recency.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.cognition.brief_models import BriefSection
from agent_memory_lite.cognition.brief_tokens import fit_to_budget

# Priority weights used to rank pinned behaviors for the brief surface.
# Mirrors PRIORITY_WEIGHT in behavior_repo_ranking but kept local so a
# refactor of either doesn't silently break the brief contract.
_PRIORITY_WEIGHT: dict[str, float] = {
    "system_bound": 4.0,
    "user_preference": 3.0,
    "project_convention": 2.0,
    "suggestion": 1.0,
}


def _iso_to_sort_key(value: str) -> float:
    """Best-effort: parse ISO timestamp to epoch seconds for ordering.
    Failures return 0.0 so the row sorts to the bottom rather than
    crashing the brief (defensive against legacy rows with NULL /
    malformed updated_at)."""
    if not value:
        return 0.0
    try:
        from datetime import datetime  # noqa: PLC0415

        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _applies_to_csv(raw: object) -> str:
    """Render applies_to_json as a short CSV (max 4 items) for the
    brief line. Best-effort: malformed JSON returns empty string."""
    if not raw:
        return ""
    try:
        import json as _json  # noqa: PLC0415

        items = _json.loads(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if not isinstance(items, list):
        return ""
    text = ", ".join(str(x) for x in items[:4])
    if len(items) > 4:
        text += ", ..."
    return text


def _collect_pinned_behaviors_both_tables(
    conn: sqlite3.Connection, workspace_id: str
) -> list[dict[str, object]]:
    """Pull pinned behaviors from BOTH ``behaviors`` and
    ``behavior_instructions`` tables.

    The brief composer used to read only ``behaviors``. Operator
    behaviors written via HTTP /memory/upsert_behavior_instruction land
    in ``behavior_instructions``; they were invisible to the agent's
    brief — the dominant cause of the 2026-05-20 push-without-approval
    incident.
    """
    out: list[dict[str, object]] = []
    # behaviors (legacy / insight-promotion path)
    try:
        rows = conn.execute(
            "SELECT id, name, rule_one_line, applies_to_json, priority, updated_at "
            "FROM behaviors WHERE workspace_id = ? AND pinned = 1 AND active = 1 "
            "LIMIT 50",
            (workspace_id,),
        ).fetchall()
        for row in rows:
            out.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "rule_one_line": row["rule_one_line"],
                    "applies_to_csv": _applies_to_csv(row["applies_to_json"]),
                    "priority": row["priority"],
                    "updated_at": row["updated_at"],
                }
            )
    except sqlite3.OperationalError:
        pass  # legacy table missing
    # behavior_instructions (canonical write surface)
    try:
        rows = conn.execute(
            "SELECT id, name, rule, applies_to_json, priority, updated_at "
            "FROM behavior_instructions WHERE workspace_id = ? "
            "AND pinned = 1 AND active = 1 LIMIT 50",
            (workspace_id,),
        ).fetchall()
        for row in rows:
            rule = row["rule"]
            rule_one_line = rule.splitlines()[0][:160] if rule else ""
            out.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "rule_one_line": rule_one_line,
                    "applies_to_csv": _applies_to_csv(row["applies_to_json"]),
                    "priority": row["priority"],
                    "updated_at": row["updated_at"],
                }
            )
    except sqlite3.OperationalError:
        pass  # pre-migration DB without behavior_instructions
    return out


def _build_pinned_behaviors(
    conn: sqlite3.Connection, workspace_id: str, budget: int
) -> BriefSection:
    """Top-N pinned behaviors with PRIORITY-WEIGHTED sort.

    v3.6 critical fix (operator incident 2026-05-20): the previous
    implementation sorted by ``updated_at DESC`` only, so recently-updated
    ``project_convention`` rules pushed safety-critical ``user_preference``
    rules out of the brief. Priority weights now mirror
    ``behavior_repo_ranking.PRIORITY_WEIGHT``: system_bound >
    user_preference > project_convention > suggestion; recency only
    breaks priority ties.
    """
    candidates = _collect_pinned_behaviors_both_tables(conn, workspace_id)
    candidates.sort(
        key=lambda b: (
            -_PRIORITY_WEIGHT.get(str(b.get("priority") or "project_convention"), 1.0),
            -_iso_to_sort_key(str(b.get("updated_at") or "")),
        )
    )
    lines = ["## Pinned behaviors"]
    seen: set[str] = set()
    for b in candidates:
        # SQL returns are typed as `object`; coerce to str at the boundary
        # so the dedup set and string ops below stay well-typed.
        name = str(b.get("name") or "?")
        if name in seen:
            continue
        seen.add(name)
        rule_raw = b.get("rule_one_line") or b.get("rule") or name
        rule = str(rule_raw) if rule_raw else ""
        # Truncate rule to a single line — full body fetched on demand.
        rule_line = rule.splitlines()[0][:160] if rule else ""
        applies = str(b.get("applies_to_csv") or "")
        line = f"- {name}: {rule_line}"
        if applies:
            line += f" (applies_to: {applies})"
        lines.append(line)
    return BriefSection(name="behaviors", budget=budget, lines=fit_to_budget(lines, budget))
