"""Per-kind compact projections — the cornerstone of v3 read economy.

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


# ============================================================
# Per-kind projections
# ============================================================


def project_decision(row: sqlite3.Row) -> dict[str, Any]:
    """Token target: ~25. {id, title, status, gist, supersedes, valid_from}."""
    return {
        "id": _row_get(row, "id"),
        "kind": "decision",
        "title": _row_get(row, "title", ""),
        "status": _row_get(row, "status", "active"),
        "gist": _row_get(row, "gist"),
        "supersedes": _row_get(row, "supersedes_decision_id"),
        "pinned": bool(_row_get(row, "pinned", 0)),
        "valid_from": _row_get(row, "valid_from"),
    }


def project_theory(row: sqlite3.Row) -> dict[str, Any]:
    """Token target: ~20. {id, claim, status, evidence_count, confidence}."""
    return {
        "id": _row_get(row, "id"),
        "kind": "theory",
        "title": _row_get(row, "title", ""),
        "gist": _row_get(row, "gist") or _row_get(row, "claim", "")[:120],
        "status": _row_get(row, "status", "proposed"),
        "evidence_count": int(_row_get(row, "evidence_count", 0)),
        "confidence": float(_row_get(row, "confidence", 0.4)),
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
        "body_token_count": int(_row_get(row, "body_token_count", 0)),
        "usage_count": int(_row_get(row, "usage_count", 0)),
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
        "symbol_count": int(_row_get(row, "symbol_count", 0)),
        "inbound_edge_count": int(_row_get(row, "inbound_edge_count", 0)),
        "pagerank": float(_row_get(row, "pagerank", 0.0)),
    }


def project_chunk(row: sqlite3.Row) -> dict[str, Any]:
    """Token target: ~30. {id, kind, gist, qualified_name, line_start}."""
    return {
        "id": _row_get(row, "id"),
        "kind": "chunk",
        "chunk_kind": _row_get(row, "kind", "doc"),
        "gist": _row_get(row, "gist") or _row_get(row, "summary"),
        "qualified_name": _row_get(row, "qualified_name"),
        "symbol_kind": _row_get(row, "symbol_kind"),
        "line_start": _row_get(row, "line_start"),
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
    "code_digest": project_code_digest,
    "chunk": project_chunk,
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
