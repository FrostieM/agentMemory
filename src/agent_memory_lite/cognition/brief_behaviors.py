"""Brief section builder for pinned behaviors, the safety-critical layer.

Extracted from cognition/brief.py during the v3.7 SLOC decomposition.
Reads pinned rows from the canonical ``behaviors`` table, dedupes, and
renders them priority-weighted so safety rules cannot be pushed out by
recency.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.cognition.brief_models import BriefSection
from agent_memory_lite.cognition.brief_tokens import fit_to_budget

# Priority weights used to rank pinned behaviors for the brief surface.
# Mirrors PRIORITY_WEIGHT in behavior_repo_ranking but kept local so a
# refactor of either does not silently break the brief contract.
_PRIORITY_WEIGHT: dict[str, float] = {
    "system_bound": 4.0,
    "user_preference": 3.0,
    "project_convention": 2.0,
    "suggestion": 1.0,
}


def _iso_to_sort_key(value: str) -> float:
    """Best-effort timestamp parse for ordering."""
    if not value:
        return 0.0
    try:
        from datetime import datetime  # noqa: PLC0415

        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _applies_to_csv(raw: object) -> str:
    """Render applies_to_json as a short CSV for the brief line."""
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


def _collect_pinned_behaviors(
    conn: sqlite3.Connection, workspace_id: str
) -> list[dict[str, object]]:
    """Pull pinned behaviors from the canonical v3 table."""
    out: list[dict[str, object]] = []
    try:
        rows = conn.execute(
            "SELECT id, name, rule, rule_one_line, applies_to_json, priority, updated_at "
            "FROM behaviors WHERE workspace_id = ? AND pinned = 1 AND active = 1 "
            "LIMIT 50",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for row in rows:
        out.append(
            {
                "id": row["id"],
                "name": row["name"],
                "rule": row["rule"],
                "rule_one_line": row["rule_one_line"],
                "applies_to_csv": _applies_to_csv(row["applies_to_json"]),
                "priority": row["priority"],
                "updated_at": row["updated_at"],
            }
        )
    return out


def _credit_behaviors_applied(
    conn: sqlite3.Connection, workspace_id: str, instruction_ids: list[str]
) -> None:
    """Record that these pinned behaviors were rendered into the envelope.

    This is the ONLY caller of ``mark_behavior_instructions_applied`` -- the
    write was lost in a refactor, freezing ``behaviors.application_count`` /
    ``last_applied_at`` and so the ``behaviors_fired_ratio`` adoption KPI and the
    behavior outcome-scoring evidence weight. Opt-in
    (``MEMORY_BEHAVIOR_APPLY_TRACKING_ENABLED``, default on) and failure-soft:
    telemetry must never break the safety-critical brief. The batched write does
    NOT touch ``behaviors.updated_at`` (see ``behavior_apply``), so it cannot
    invalidate the brief cache.
    """
    ids = [i for i in instruction_ids if i]
    if not ids:
        return
    try:
        from agent_memory_lite.config.settings import get_settings  # noqa: PLC0415

        if not get_settings().behavior_apply_tracking_enabled:
            return
        from agent_memory_lite.capability.behavior_apply import (  # noqa: PLC0415
            mark_behavior_instructions_applied,
        )

        mark_behavior_instructions_applied(conn, workspace_id=workspace_id, instruction_ids=ids)
        conn.commit()
    except Exception:  # pragma: no cover - telemetry is best-effort
        pass


def _build_pinned_behaviors(
    conn: sqlite3.Connection, workspace_id: str, budget: int
) -> BriefSection:
    """Top-N pinned behaviors with priority-weighted sort."""
    candidates = _collect_pinned_behaviors(conn, workspace_id)
    candidates.sort(
        key=lambda b: (
            -_PRIORITY_WEIGHT.get(str(b.get("priority") or "project_convention"), 1.0),
            -_iso_to_sort_key(str(b.get("updated_at") or "")),
        )
    )
    lines = ["## Pinned behaviors"]
    rendered_ids: list[str] = []
    seen: set[str] = set()
    for b in candidates:
        # SQL returns are typed as object; coerce to str at the boundary
        # so the dedup set and string ops below stay well-typed.
        name = str(b.get("name") or "?")
        if name in seen:
            continue
        seen.add(name)
        rule_raw = b.get("rule_one_line") or b.get("rule") or name
        rule = str(rule_raw) if rule_raw else ""
        rule_line = rule.splitlines()[0][:160] if rule else ""
        applies = str(b.get("applies_to_csv") or "")
        line = f"- {name}: {rule_line}"
        if applies:
            line += f" (applies_to: {applies})"
        lines.append(line)
        rendered_ids.append(str(b.get("id") or ""))
    fitted = fit_to_budget(lines, budget)
    # Credit the behaviors whose bullet survived the budget as "applied" (rendered
    # into the agent's envelope). fit_to_budget tail-trims, so the survivors are
    # the leading ids; fitted[0] is the header. May credit up to 2x per brief when
    # the redistribution pass rebuilds this section -- harmless: the KPI is
    # count>0, unchanged by the multiplier.
    _credit_behaviors_applied(conn, workspace_id, rendered_ids[: max(0, len(fitted) - 1)])
    return BriefSection(name="behaviors", budget=budget, lines=fitted)
