"""Phase 3 of the memory-as-brain evolution: promote durable insights.

A consolidation insight that survives review (high confidence, surfaced
in the brief multiple times without being archived) is no longer an
"observation" -- it is an installed operating rule. Promote it into a
pinned behavior so the agent applies it on every future call.

Promotion gate: ``confidence >= MIN_CONFIDENCE`` AND
``surface_count >= MIN_SURFACE_EVENTS`` AND status='candidate' (not yet
promoted nor rejected). Both gates default to the conservative values
specified in the plan (0.7 + 2), so a single high-confidence run is
not enough to materialize a rule.

The promotion is **append-only**:
- A new ``behaviors`` row is inserted with ``pinned=1``,
  ``priority='project_convention'``, ``source_type='insight'``,
  ``source_id=<insight_id>``.
- The insight is marked ``status='promoted'`` so the gate can never
  re-fire for the same row.
- No existing behavior is mutated; conflicts are handled by the
  existing ``conflict_policy`` machinery during brief render.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

MIN_CONFIDENCE = 0.7
MIN_SURFACE_EVENTS = 2

# Round-2 audit (H2): consolidation's heuristic fallback
# (consolidation._heuristic_summary) emits a token-frequency CSV —
# "Recurring theme (5 episodes): docs, changelog, file_indexed". That
# is a token bag, never an operating rule. It is written at
# confidence=0.55 so it normally can't pass the gate, but if any path
# bumps the confidence it would install a GARBAGE pinned behavior that
# then rides every brief. Refuse to promote an insight whose summary
# is this heuristic-fallback shape. LLM-distilled consolidation
# insights ("Pattern: ...") and behavior_reinforcement insights are
# unaffected — they don't match this regex.
_HEURISTIC_NOISE_RE = re.compile(r"^\s*Recurring theme\s*\(\d+\s*episode", re.IGNORECASE)


def _is_promotable_summary(summary: str | None) -> bool:
    """False for an empty summary or the heuristic token-CSV shape."""
    text = (summary or "").strip()
    if not text:
        return False
    return _HEURISTIC_NOISE_RE.match(text) is None


@dataclass(frozen=True, slots=True)
class PromotionStats:
    """Per-workspace promotion summary."""

    inspected: int
    promoted: int
    skipped: int


def _eligible_insights(conn: sqlite3.Connection, *, workspace_id: str) -> list[sqlite3.Row]:
    """Return candidate insights that satisfy the promotion gate.

    Pre-migration-safe: returns empty list when surface_count column
    doesn't exist yet (operator hasn't run 0004 migration).
    """
    try:
        rows = conn.execute(
            """
            SELECT id, insight_type, summary, gist, confidence, surface_count,
                   source_episode_ids_json, tags_json
              FROM insights
             WHERE workspace_id = ?
               AND status = 'candidate'
               AND confidence >= ?
               AND surface_count >= ?
             ORDER BY confidence DESC, surface_count DESC
            """,
            (workspace_id, MIN_CONFIDENCE, MIN_SURFACE_EVENTS),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    # H2 gate: drop heuristic token-CSV insights — they must never
    # become a pinned behavior even if their confidence was bumped.
    return [r for r in rows if _is_promotable_summary(r["summary"])]


def _behavior_already_exists(
    conn: sqlite3.Connection, *, workspace_id: str, insight_id: str
) -> bool:
    """Idempotency guard -- skip if a behavior already sourced from this insight."""
    row = conn.execute(
        "SELECT 1 FROM behaviors "
        "WHERE workspace_id = ? AND source_type = 'insight' AND source_id = ? LIMIT 1",
        (workspace_id, insight_id),
    ).fetchone()
    return row is not None


def _insert_behavior_from_insight(
    conn: sqlite3.Connection, *, workspace_id: str, insight_row: sqlite3.Row
) -> str:
    """Insert one pinned behavior row sourced from an insight. Returns behavior id."""
    # v3.5 sector-3 audit-followup: redact the insight summary BEFORE it
    # becomes a pinned behavior. Episode-derived insights may carry
    # secrets the consolidation LLM rephrased past the original
    # redaction; without this redact() they'd land in behaviors.rule
    # and surface verbatim in every future brief / envelope.
    from agent_memory_lite.redaction.redactor import redact  # noqa: PLC0415

    behavior_id = new_id(IdKind.BEHAVIOR_INSTRUCTION)
    now = iso_now()
    raw_body = insight_row["summary"] or insight_row["gist"] or "auto-promoted insight"
    raw_one_line = (insight_row["gist"] or insight_row["summary"] or "")[:160]
    body = redact(raw_body).text
    one_line = redact(raw_one_line).text
    name = f"insight-{insight_row['id'][-12:]}"
    conn.execute(
        """
        INSERT INTO behaviors (
            id, workspace_id, name, kind, scope, priority, rule, rule_one_line,
            rationale, applies_to_json, conflict_policy, source_type, source_id,
            confidence, importance, pinned, active, created_at, updated_at
        ) VALUES (?, ?, ?, 'operating_rule', 'workspace', 'project_convention',
                  ?, ?, 'auto-promoted from consolidation insight', '[]',
                  'current_user_wins', 'insight', ?, ?, 0.7, 1, 1, ?, ?)
        """,
        (
            behavior_id,
            workspace_id,
            name,
            body,
            one_line,
            insight_row["id"],
            float(insight_row["confidence"] or 0.7),
            now,
            now,
        ),
    )
    conn.execute(
        "UPDATE insights SET status = 'promoted', updated_at = ? WHERE id = ?",
        (now, insight_row["id"]),
    )
    return behavior_id


def promote_eligible_insights(conn: sqlite3.Connection, *, workspace_id: str) -> PromotionStats:
    """Scan eligible insights and promote each to a pinned behavior.

    Idempotent: an insight that already produced a behavior is marked
    ``status='promoted'`` and the gate query never re-selects it. A
    second call on the same workspace performs no work.
    """
    # v3.5 sector-3 audit-followup: wrap the loop in with_tx so a
    # caller wrapping us inside an outer transaction cannot prematurely
    # flush via the previous bare ``conn.commit()``. ``with_tx`` uses
    # SAVEPOINTs when nested so this stays composable.
    from agent_memory_lite.db.transactions import with_tx  # noqa: PLC0415

    rows = _eligible_insights(conn, workspace_id=workspace_id)
    promoted = 0
    skipped = 0
    with with_tx(conn):
        for row in rows:
            if _behavior_already_exists(conn, workspace_id=workspace_id, insight_id=row["id"]):
                skipped += 1
                continue
            try:
                _insert_behavior_from_insight(conn, workspace_id=workspace_id, insight_row=row)
                promoted += 1
            except sqlite3.Error:
                skipped += 1
                continue
    return PromotionStats(inspected=len(rows), promoted=promoted, skipped=skipped)
