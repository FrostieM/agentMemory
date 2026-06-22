"""Per-kind compact projections — the cornerstone of the read-economy.

Every retrieval-eligible kind has a projection function that converts
a sqlite3.Row into the minimum-viable ~20-40 token dict the agent
needs to decide whether to fetch the full body.

These are the DEFAULT shapes returned by ``memory_search`` /
``memory_get`` / ``memory_brief``. Full bodies are opt-in via
``memory_get(id, fields=['rationale', 'decision_text'])``.

Token targets (per docs/V3_SCHEMA.md):
  Decision    ~25   Theory     ~20   Behavior    ~30
  Skill       ~20   Episode    ~15   Concept     ~20
  Code digest ~40   Task       ~25   Insight     ~20

Projections are pure functions over sqlite3.Row. No DB access here —
the reader module assembles them from SELECT results.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_memory_lite.utils.text_encoding import repair_common_mojibake

# ============================================================
# Helpers
# ============================================================


def _row_get(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    """Safe row access — returns default if column absent or NULL."""
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return value if value is not None else default


def _as_float(value: Any, default: float = 0.0) -> float:
    """Coerce a stored column value to float, falling back to ``default`` on a
    non-numeric value. A numeric column whitelisted by the writer can still hold
    a poisoned TEXT value if it was written before value-validation existed (or
    via a path that bypasses it); a bare ``float()`` over that row would raise
    and durably crash EVERY projection of the kind (and thus memory_brief /
    memory_search / memory_get). This keeps a single bad row from doing that."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    """Integer counterpart of :func:`_as_float` — see its docstring."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))  # tolerate '3.0'-style stored values
        except (TypeError, ValueError):
            return default


def _repair_text(value: Any) -> Any:
    return repair_common_mojibake(value) if isinstance(value, str) else value


def _csv(json_str: str | None, limit: int = 5) -> str:
    """Convert a JSON array to a CSV string capped at ``limit`` items."""
    if not json_str:
        return ""
    try:
        items = json.loads(json_str)
    except (TypeError, ValueError):
        return ""
    if not isinstance(items, list):
        return ""
    out = [str(item) for item in items[:limit]]
    return ", ".join(out)


def _truncate(value: Any, limit: int = 120) -> Any:
    """Trim a long string projection to ``limit`` chars on a word boundary.

    Used only for chunk gists, which are the first ~200 chars of a raw code
    chunk (``code_indexer`` stores ``text[:200]``), so the tail is low-signal
    mid-body code, frequently cut mid-token. memory_search returns many chunks,
    so trimming the display gist to ~120 chars cuts result tokens ~40% with no
    information loss -- the full chunk text stays fetchable via
    ``memory_get(kind="chunk", fields=["text"])``. Non-strings and already-short
    values pass through unchanged (so a NULL gist still projects as None).
    """
    if not isinstance(value, str) or len(value) <= limit:
        return value
    cut = value[:limit]
    space = cut.rfind(" ")
    if space >= limit // 2:  # break on a word boundary, but keep >= half the budget
        cut = cut[:space]
    return cut.rstrip() + "..."


# ============================================================
# Per-kind projections
# ============================================================


def project_decision(row: sqlite3.Row) -> dict[str, Any]:
    """Token target: ~25. {id, title, status, gist, supersedes, valid_from}."""
    return {
        "id": _row_get(row, "id"),
        "kind": "decision",
        "title": _repair_text(_row_get(row, "title", "")),
        "status": _row_get(row, "status", "active"),
        "gist": _repair_text(_row_get(row, "gist")),
        "supersedes": _row_get(row, "supersedes_decision_id"),
        "pinned": bool(_row_get(row, "pinned", 0)),
        "valid_from": _row_get(row, "valid_from"),
        # Phase 1 outcome-loop: surfaced so brief / lint can sort by it.
        "outcome_score": _as_float(_row_get(row, "outcome_score"), 0.0),
    }


def project_theory(row: sqlite3.Row) -> dict[str, Any]:
    """Token target: ~20. {id, claim, status, evidence_count, confidence}."""
    return {
        "id": _row_get(row, "id"),
        "kind": "theory",
        "title": _row_get(row, "title", ""),
        "gist": _row_get(row, "gist") or _row_get(row, "claim", "")[:120],
        "status": _row_get(row, "status", "proposed"),
        "evidence_count": _as_int(_row_get(row, "evidence_count"), 0),
        "confidence": _as_float(_row_get(row, "confidence"), 0.4),
        "outcome_score": _as_float(_row_get(row, "outcome_score"), 0.0),
    }


def project_behavior(row: sqlite3.Row) -> dict[str, Any]:
    """Token target: ~30. {id, name, rule_one_line, applies_to_csv, pinned}."""
    return {
        "id": _row_get(row, "id"),
        "kind": "behavior",
        "name": _row_get(row, "name", ""),
        "rule_one_line": _row_get(row, "rule_one_line"),
        "applies_to_csv": _csv(_row_get(row, "applies_to_json"), limit=4),
        "pinned": bool(_row_get(row, "pinned", 0)),
        "behavior_kind": _row_get(row, "kind", "operating_rule"),
        # active=0 is a behavior's terminal/dead state -- behaviors have no
        # 'status' column. Surfaced so discredited-row filters can drop a
        # deactivated rule: a deactivated behavior keeps its default 0.0
        # outcome_score, so an outcome-only gate would miss it. Default 1
        # (pre-migration safe -- a missing column reads as active, never
        # wrongly discredited).
        "active": bool(_row_get(row, "active", 1)),
        "outcome_score": _as_float(_row_get(row, "outcome_score"), 0.0),
    }


def project_skill(row: sqlite3.Row) -> dict[str, Any]:
    """Token target: ~20. {id, name, when_to_use_short, body_token_count, subtype}.

    Body is NOT returned — fetched only via ``memory_invoke_skill(id)``.
    """
    return {
        "id": _row_get(row, "id"),
        "kind": "skill",
        "subtype": _row_get(row, "subtype", "skill"),
        "name": _row_get(row, "name", ""),
        "when_to_use_short": _row_get(row, "when_to_use_short"),
        "body_token_count": _as_int(_row_get(row, "body_token_count"), 0),
        "usage_count": _as_int(_row_get(row, "usage_count"), 0),
        # active=0 is a skill's terminal/dead state (memory_archive soft-archives
        # behaviors/skills/concepts to active=0; skills have no 'status' column).
        # Surfaced so discredited-row filters drop an archived skill. Default 1,
        # pre-migration safe -- see project_behavior.
        "active": bool(_row_get(row, "active", 1)),
    }


def project_episode(row: sqlite3.Row) -> dict[str, Any]:
    """Token target: ~15. {id, ts, source_type, gist}. Raw text NOT returned."""
    return {
        "id": _row_get(row, "id"),
        "kind": "episode",
        "ts": _row_get(row, "created_at"),
        "source_type": _row_get(row, "source_type", "agent_action"),
        "gist": _row_get(row, "gist"),
        "task_id": _row_get(row, "task_id"),
        # is_archived=1 is an episode's terminal/dead state. Surfaced so
        # discredited-row filters drop an archived episode reached as a brief
        # associate. Default 0, pre-migration safe.
        "is_archived": bool(_row_get(row, "is_archived", 0)),
    }


def project_concept(row: sqlite3.Row) -> dict[str, Any]:
    """Token target: ~20. {id, name, definition_one_line, aliases_csv}."""
    return {
        "id": _row_get(row, "id"),
        "kind": "concept",
        "name": _row_get(row, "name", ""),
        "definition_one_line": _row_get(row, "definition_one_line"),
        "concept_kind": _row_get(row, "kind", "term"),
        "aliases_csv": _csv(_row_get(row, "aliases_json"), limit=3),
        # active=0 is a concept's terminal/dead state (memory_archive soft-
        # archives behaviors/skills/concepts to active=0; concepts have no
        # 'status' column). Surfaced so discredited-row filters drop an archived
        # concept. Default 1, pre-migration safe -- see project_behavior.
        "active": bool(_row_get(row, "active", 1)),
    }


def project_task(row: sqlite3.Row) -> dict[str, Any]:
    """Token target: ~25. {id, goal_one_line, status, next_action, blockers_count}."""
    blockers_json = _row_get(row, "blockers_json")
    blockers_count = 0
    if blockers_json:
        try:
            blockers_count = len(json.loads(blockers_json))
        except (TypeError, ValueError):
            blockers_count = 0
    return {
        "id": _row_get(row, "id"),
        "kind": "task",
        "task_id": _row_get(row, "task_id"),
        "goal_one_line": _row_get(row, "goal_one_line"),
        "status": _row_get(row, "status", "unknown"),
        "next_action": _row_get(row, "next_action"),
        "blockers_count": blockers_count,
    }


def project_insight(row: sqlite3.Row) -> dict[str, Any]:
    """Token target: ~20. {id, insight_type, status, gist}."""
    return {
        "id": _row_get(row, "id"),
        "kind": "insight",
        "insight_type": _row_get(row, "insight_type", "observation"),
        "status": _row_get(row, "status", "new"),
        "gist": _row_get(row, "gist") or _row_get(row, "summary", "")[:120],
        "target_type": _row_get(row, "target_type"),
        "target_id": _row_get(row, "target_id"),
    }


def project_snapshot(row: sqlite3.Row) -> dict[str, Any]:
    """Token target: ~25. {id, snapshot_key, title, source_label, total_rows}."""
    return {
        "id": _row_get(row, "id"),
        "kind": "snapshot",
        "snapshot_key": _row_get(row, "snapshot_key", ""),
        "title": _row_get(row, "title", ""),
        "source_label": _row_get(row, "source_label"),
        "total_rows": _as_int(_row_get(row, "total_rows"), 0),
    }


def project_code_digest(row: sqlite3.Row) -> dict[str, Any]:
    """Token target: ~40. {file_path, purpose_short, top_symbols_csv, language}."""
    top_symbols_json = _row_get(row, "top_symbols_json")
    top_symbols_csv = ""
    if top_symbols_json:
        try:
            symbols = json.loads(top_symbols_json)
            if isinstance(symbols, list):
                names = [s.get("name", "") if isinstance(s, dict) else str(s) for s in symbols[:5]]
                top_symbols_csv = ", ".join(n for n in names if n)
        except (TypeError, ValueError):
            top_symbols_csv = ""
    return {
        "kind": "code_digest",
        "file_path": _row_get(row, "file_path", ""),
        "purpose_short": _row_get(row, "purpose_short"),
        "language": _row_get(row, "language"),
        "top_symbols_csv": top_symbols_csv,
        "symbol_count": _as_int(_row_get(row, "symbol_count"), 0),
        "inbound_edge_count": _as_int(_row_get(row, "inbound_edge_count"), 0),
        "pagerank": _as_float(_row_get(row, "pagerank"), 0.0),
    }


def project_chunk(row: sqlite3.Row) -> dict[str, Any]:
    """Token target: ~30. {id, kind, gist, qualified_name, line_start}."""
    return {
        "id": _row_get(row, "id"),
        "kind": "chunk",
        "chunk_kind": _row_get(row, "kind", "doc"),
        "gist": _truncate(_row_get(row, "gist") or _row_get(row, "summary")),
        "qualified_name": _row_get(row, "qualified_name"),
        "symbol_kind": _row_get(row, "symbol_kind"),
        "line_start": _row_get(row, "line_start"),
        # is_archived=1 is a chunk's terminal/dead state. Surfaced so
        # discredited-row filters drop an archived chunk reached as a brief
        # associate. Default 0, pre-migration safe.
        "is_archived": bool(_row_get(row, "is_archived", 0)),
    }


def project_plan_step(row: sqlite3.Row) -> dict[str, Any]:
    """Token target: ~25. {id, task_id, title, status, rank, parent_step_id}."""
    return {
        "id": _row_get(row, "id"),
        "kind": "plan_step",
        "task_id": _row_get(row, "task_id"),
        "title": _row_get(row, "title", ""),
        "status": _row_get(row, "status", "pending"),
        "rank": _row_get(row, "rank"),
        "parent_step_id": _row_get(row, "parent_step_id"),
    }


def project_issue(row: sqlite3.Row) -> dict[str, Any]:
    """Token target: ~25. {id, category, severity, status, gist}."""
    return {
        "id": _row_get(row, "id"),
        "kind": "issue",
        "category": _row_get(row, "category", "bug"),
        "severity": _row_get(row, "severity", "minor"),
        "status": _row_get(row, "status", "open"),
        "gist": _repair_text(_row_get(row, "gist") or _row_get(row, "title", "")),
    }


# ============================================================
# Dispatch by kind
# ============================================================

_PROJECTORS = {
    "decision": project_decision,
    "theory": project_theory,
    "behavior": project_behavior,
    "skill": project_skill,
    "episode": project_episode,
    "concept": project_concept,
    "task": project_task,
    "insight": project_insight,
    "issue": project_issue,
    "snapshot": project_snapshot,
    "code_digest": project_code_digest,
    "chunk": project_chunk,
    "plan_step": project_plan_step,
}


def project(kind: str, row: sqlite3.Row) -> dict[str, Any] | None:
    """Dispatch to per-kind projector. Returns None for unknown kind."""
    projector = _PROJECTORS.get(kind)
    if projector is None:
        return None
    return projector(row)


def known_kinds() -> tuple[str, ...]:
    """Return the tuple of kinds we know how to project."""
    return tuple(_PROJECTORS.keys())
