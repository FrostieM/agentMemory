"""Lightweight contradiction detector for decisions and theories.

When the agent writes a decision or theory whose title closely
overlaps an existing active item in the same workspace, the two are
likely either:

* a duplicate that should reference the existing item via
  ``supersedes_decision_id`` / ``supersedes_theory_id``, or
* an actual contradiction of the existing item that should be
  resolved by the user.

This module computes a Jaccard word-overlap score between the new
text and each existing item's title+claim, and writes a
``potential_conflict`` maintenance event when any score is above the
configured threshold. The event details carry both ids so the agent
can call ``memory_get_object`` on either side and resolve the conflict.

Off by default - enabled via MEMORY_CONFLICT_DETECT_ENABLED=1. The
heuristic is intentionally conservative: it never blocks a write.
"""

from __future__ import annotations

import re
import sqlite3

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.ingestion.maintenance_writer import write_maintenance_event
from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.models.enums import MaintenanceSeverity
from agent_memory_lite.models.maintenance import MaintenanceEventIn

_log = get_logger("ingestion.conflict_detect")
_TOKEN_RE = re.compile(r"\w{3,}", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in _TOKEN_RE.finditer(text or "")}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def detect_conflicts(
    conn: sqlite3.Connection,
    *,
    settings: Settings | None,
    workspace_id: str,
    target_type: str,
    target_id: str,
    target_text: str,
) -> list[tuple[str, float]]:
    """Scan existing same-kind items and write a potential_conflict
    event when overlap >= threshold. Returns the (id, score) pairs
    that triggered.

    target_type: ``"decision"`` or ``"theory"``.
    """
    if settings is None or not settings.conflict_detect_enabled:
        return []
    new_tokens = _tokens(target_text)
    if not new_tokens:
        return []
    table, text_cols = {
        "decision": ("decisions", ("title", "decision_text")),
        "theory": ("theories", ("title", "claim")),
    }.get(target_type, ("", ()))
    if not table:
        return []
    # Only compare against items that are still considered live.
    # Archived / superseded / rejected rows are not load-bearing and
    # would generate false positives ("new theory overlaps a theory
    # we already retired").
    status_clause = (
        "status = 'active'"
        if target_type == "decision"
        else "status NOT IN ('archived','rejected','superseded')"
    )
    sql = (
        "SELECT id, "
        + ", ".join(text_cols)
        + f" FROM {table} WHERE workspace_id = ? AND id != ? AND "
        + status_clause
    )
    try:
        rows = conn.execute(sql, (workspace_id, target_id)).fetchall()
    except sqlite3.DatabaseError as exc:
        _log.warning("conflict_scan_failed", error=str(exc))
        return []

    threshold = settings.conflict_detect_threshold
    matches: list[tuple[str, float]] = []
    for row in rows:
        existing_tokens: set[str] = set()
        for col in text_cols:
            existing_tokens |= _tokens(str(row[col] or ""))
        score = _jaccard(new_tokens, existing_tokens)
        if score >= threshold:
            matches.append((str(row["id"]), score))
    if not matches:
        return []
    matches.sort(key=lambda pair: pair[1], reverse=True)
    write_maintenance_event(
        conn,
        MaintenanceEventIn(
            workspace_id=workspace_id,
            kind="potential_conflict",
            severity=MaintenanceSeverity.WARNING,
            summary=(
                f"New {target_type} {target_id} overlaps {len(matches)} existing "
                f"{target_type}(s) by Jaccard >= {threshold:.2f}; review for "
                "supersedes-link or contradiction."
            ),
            details={
                "target_type": target_type,
                "target_id": target_id,
                "matches": [{"id": mid, "jaccard": round(score, 3)} for mid, score in matches],
            },
            target_type=target_type,
            target_id=target_id,
        ),
    )
    return matches
