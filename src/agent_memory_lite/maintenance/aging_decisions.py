"""Surface decisions written long ago with zero accumulated feedback.

MVP for the agent-feedback "outcome ping" ask: when an architectural
decision was written N+ days ago but ``outcome_score`` is still exactly
0.0 (no implicit/explicit feedback ever landed), the agent has no way
to know whether the decision was validated, contradicted, or simply
forgotten. This module turns "30 days of silence on a decision" into a
brief section so the next session prompt asks "still active?".

Read-only — never archives, never degrades outcome_score, never
auto-supersedes. The signal is "look at this", not "act on this".

Settings:

* ``MEMORY_AGING_DECISIONS_ENABLED`` (default ``true``) — master switch.
* ``MEMORY_AGING_DECISIONS_DAYS`` (default ``30``) — age threshold.
* ``MEMORY_AGING_DECISIONS_LIMIT`` (default ``3``) — max rows per brief.

Pinned rows are excluded (operator-anchored decisions are durable by
design). Archived / superseded rows are excluded (the decision already
has an outcome — superseding it IS the feedback). Rows with non-zero
``outcome_score`` are excluded (they already accumulated feedback).
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from agent_memory_lite.utils.time import parse_iso


@dataclass(frozen=True, slots=True)
class AgingDecision:
    decision_id: str
    title: str
    snippet: str
    age_days: int
    confidence: float


def is_enabled() -> bool:
    raw = os.environ.get("MEMORY_AGING_DECISIONS_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _read_int_env(name: str, default: int, *, floor: int = 1) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(floor, value)


def threshold_days() -> int:
    return _read_int_env("MEMORY_AGING_DECISIONS_DAYS", 30, floor=1)


def emit_limit() -> int:
    return _read_int_env("MEMORY_AGING_DECISIONS_LIMIT", 3, floor=1)


def find_aging_decisions(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    older_than_days: int | None = None,
    limit: int | None = None,
) -> list[AgingDecision]:
    """Active decisions older than threshold with outcome_score == 0.0."""
    days = older_than_days if older_than_days is not None else threshold_days()
    cap = limit if limit is not None else emit_limit()
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    try:
        rows = conn.execute(
            """
            SELECT id, title, decision_text, valid_from, created_at, confidence
              FROM decisions
             WHERE workspace_id = ?
               AND status = 'active'
               AND COALESCE(pinned, 0) = 0
               AND COALESCE(outcome_score, 0.0) = 0.0
               AND COALESCE(valid_from, created_at) < ?
             ORDER BY COALESCE(valid_from, created_at) ASC
             LIMIT ?
            """,
            (workspace_id, cutoff, cap),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    now = datetime.now(UTC)
    out: list[AgingDecision] = []
    for row in rows:
        ref = row["valid_from"] or row["created_at"]
        try:
            written = parse_iso(str(ref))
            # ``timedelta.days`` floors to whole days using datetime
            # semantics (not truncate-toward-zero on a float). Matches
            # operator expectations: a row written 30.001 days ago
            # reports age_days=30, not 30 — wait, that IS what int()
            # does. The cosmetic fix from the audit is preferring the
            # native ``.days`` accessor over manual division so the
            # intent is clearer in code review.
            age_days = (now - written).days
        except (ValueError, TypeError):
            # Unparseable timestamp — should be rare (always written by
            # iso_now()). Use -1 as a sentinel so the brief renderer can
            # distinguish "stale ID" from "exactly N days old".
            age_days = -1
        body = str(row["decision_text"] or "")
        out.append(
            AgingDecision(
                decision_id=str(row["id"]),
                title=str(row["title"] or ""),
                snippet=body[:120],
                age_days=age_days,
                confidence=float(row["confidence"] or 0.0),
            )
        )
    return out
