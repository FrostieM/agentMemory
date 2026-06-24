"""Recent duplicate-episode detection for memory hygiene."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from agent_memory_lite.maintenance.hygiene_models import (
    HygieneFinding,
    parse_iso,
    table_exists,
    tokens_from,
)


def _token_overlap(left: str, right: str) -> float:
    left_tokens = tokens_from(left)
    right_tokens = tokens_from(right)
    denom = min(len(left_tokens), len(right_tokens))
    if denom < 8:
        return 0.0
    return len(left_tokens & right_tokens) / denom


def find_recent_duplicate_episode_gaps(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    lookback_limit: int = 500,
    window_seconds: int = 10,
    overlap_threshold: float = 0.6,
) -> list[HygieneFinding]:
    if not table_exists(conn, "episodes"):
        return []
    rows = conn.execute(
        """
        SELECT id, task_id, source_type, raw_text, created_at
        FROM episodes
        WHERE workspace_id = ?
          AND is_archived = 0
          AND source_type = 'agent_action'
          AND COALESCE(gist, '') = ''
          AND COALESCE(summary, '') = ''
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (workspace_id, lookback_limit),
    ).fetchall()
    findings: list[HygieneFinding] = []
    window = timedelta(seconds=window_seconds)
    for idx, row in enumerate(rows):
        created = parse_iso(str(row["created_at"]))
        if created is None:
            continue
        for other in rows[idx + 1 :]:
            if row["task_id"] != other["task_id"] or row["source_type"] != other["source_type"]:
                continue
            other_created = parse_iso(str(other["created_at"]))
            if other_created is None or abs(created - other_created) > window:
                continue
            overlap = _token_overlap(str(row["raw_text"]), str(other["raw_text"]))
            if overlap < overlap_threshold:
                continue
            findings.append(
                HygieneFinding(
                    kind="recent_duplicate_episode",
                    severity="warning",
                    target_type="episode",
                    target_id=str(row["id"]),
                    summary="Recent agent_action episodes look duplicate and should be merged or archived.",
                    details={
                        "matched_episode_id": str(other["id"]),
                        "task_id": row["task_id"],
                        "created_at": row["created_at"],
                        "matched_created_at": other["created_at"],
                        "token_overlap": round(overlap, 3),
                    },
                )
            )
            break
    return findings
