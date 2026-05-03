"""Exponentially weighted moving average over feedback rows.

The aggregator reads ``memory_usage_feedback`` and writes the resulting EWMA
into the denormalised ``feedback_ewma`` column on chunks/decisions/theories,
so the retrieval scoring formula can use it without an extra join. Pure
function ``compute_ewma`` is unit-testable independently of the database.

Mitigations baked in (per v1.4 plan blind spot 1):
- Optional self-loop exclusion: feedback rows whose ``source`` equals
  ``self_loop`` are skipped.
- Per-day-per-source cap: at most N rows per (source, day) bucket
  contribute, preventing a flood of synthetic +1 feedback from inflating
  EWMA in a single session.
- EWMA stays bounded in [-1, 1] because raw usefulness is bounded.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.utils.time import iso_now, now, parse_iso

SUPPORTED_SOURCE_TYPES = ("chunk", "decision", "theory")
SOURCE_TYPE_TO_TABLE = {"chunk": "chunks", "decision": "decisions", "theory": "theories"}
SELF_LOOP_SOURCE = "self_loop"


@dataclass(frozen=True, slots=True)
class FeedbackRow:
    source_id: str
    usefulness: float
    created_at: str
    source: str


@dataclass(frozen=True, slots=True)
class EwmaResult:
    source_id: str
    ewma: float
    sample_count: int


def _decay_weight(age_days: float, half_life_days: float) -> float:
    return math.pow(0.5, age_days / half_life_days)


def _cap_per_day_per_source(
    rows: Iterable[FeedbackRow], *, max_per_day_per_source: int
) -> list[FeedbackRow]:
    bucket: dict[tuple[str, str, str], int] = defaultdict(int)
    keep: list[FeedbackRow] = []
    for row in rows:
        try:
            day = parse_iso(row.created_at).date().isoformat()
        except ValueError:
            day = row.created_at[:10]
        key = (row.source_id, row.source, day)
        if bucket[key] >= max_per_day_per_source:
            continue
        bucket[key] += 1
        keep.append(row)
    return keep


def compute_ewma(
    rows: Iterable[FeedbackRow],
    *,
    half_life_days: float,
    reference: datetime | None = None,
    exclude_self_loop: bool = True,
    max_per_day_per_source: int = 10,
) -> dict[str, EwmaResult]:
    """Aggregate feedback rows into per-source EWMA values.

    Pure function — does not touch the database. Sort order does not matter.
    """
    ref = reference if reference is not None else now()
    filtered = [r for r in rows if not (exclude_self_loop and r.source == SELF_LOOP_SOURCE)]
    capped = _cap_per_day_per_source(filtered, max_per_day_per_source=max_per_day_per_source)

    weighted_sum: dict[str, float] = defaultdict(float)
    weight_total: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for row in capped:
        try:
            ts = parse_iso(row.created_at)
        except ValueError:
            continue
        age_days = max(0.0, (ref - ts).total_seconds() / 86400.0)
        weight = _decay_weight(age_days, half_life_days)
        weighted_sum[row.source_id] += weight * row.usefulness
        weight_total[row.source_id] += weight
        counts[row.source_id] += 1

    out: dict[str, EwmaResult] = {}
    for source_id, total in weight_total.items():
        if total <= 0.0:
            continue
        ewma = max(-1.0, min(1.0, weighted_sum[source_id] / total))
        out[source_id] = EwmaResult(source_id=source_id, ewma=ewma, sample_count=counts[source_id])
    return out


def _load_rows(
    conn: sqlite3.Connection, *, workspace_id: str, source_type: str
) -> list[FeedbackRow]:
    rows = conn.execute(
        """
        SELECT source_id, usefulness, created_at, source
        FROM memory_usage_feedback
        WHERE workspace_id = ? AND source_type = ?
        """,
        (workspace_id, source_type),
    ).fetchall()
    return [
        FeedbackRow(
            source_id=str(row["source_id"]),
            usefulness=float(row["usefulness"]),
            created_at=str(row["created_at"]),
            source=str(row["source"]),
        )
        for row in rows
    ]


def recompute_workspace_ewma(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    source_type: str,
    half_life_days: float,
    exclude_self_loop: bool = True,
    max_per_day_per_source: int = 10,
) -> dict[str, EwmaResult]:
    """Recompute EWMA for every source_id in a workspace and update the column."""
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise ValueError(f"unsupported source_type: {source_type!r}")
    table = SOURCE_TYPE_TO_TABLE[source_type]
    rows = _load_rows(conn, workspace_id=workspace_id, source_type=source_type)
    results = compute_ewma(
        rows,
        half_life_days=half_life_days,
        exclude_self_loop=exclude_self_loop,
        max_per_day_per_source=max_per_day_per_source,
    )
    if not results:
        return results
    updates = [(result.ewma, sid, workspace_id) for sid, result in results.items()]
    conn.executemany(
        f"UPDATE {table} SET feedback_ewma = ? WHERE id = ? AND workspace_id = ?",
        updates,
    )
    insert_audit(
        conn,
        workspace_id=workspace_id,
        action="feedback.ewma_recomputed",
        target_type=source_type,
        target_id=workspace_id,
        after={"recomputed_count": len(results), "at": iso_now()},
    )
    return results
