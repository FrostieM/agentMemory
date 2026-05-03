"""Persist watchdog sentinel results + compute trends (v1.9).

The watchdog runs retrieval-quality cases and prints pass/fail; v1.9 lets
it persist each result so /memory/sentinel_trends can show regressions
over a window. A run_id ties together cases from one watchdog invocation.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


@dataclass(frozen=True, slots=True)
class SentinelResultIn:
    case_name: str
    status: str
    matched_count: int = 0
    expected_count: int = 0
    failures: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


def record_sentinel_run(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    results: list[SentinelResultIn],
    run_id: str | None = None,
) -> tuple[str, list[str]]:
    """Insert one sentinel_results row per case + a single audit row covering
    the whole run. Returns (run_id, list of new result_ids)."""
    if not results:
        return (run_id or "", [])
    actual_run_id = run_id or new_id(IdKind.SENTINEL_RESULT)
    now_iso = iso_now()
    new_ids: list[str] = []
    for result in results:
        result_id = new_id(IdKind.SENTINEL_RESULT)
        conn.execute(
            """
            INSERT INTO retrieval_sentinel_results
            (id, workspace_id, case_name, status, matched_count,
             expected_count, failures_json, metrics_json, run_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result_id,
                workspace_id,
                result.case_name,
                result.status,
                result.matched_count,
                result.expected_count,
                json.dumps(list(result.failures), default=str),
                json.dumps(result.metrics, sort_keys=True, default=str),
                actual_run_id,
                now_iso,
            ),
        )
        new_ids.append(result_id)
    insert_audit(
        conn,
        workspace_id=workspace_id,
        action="sentinel.run_recorded",
        target_type="workspace",
        target_id=workspace_id,
        after={
            "run_id": actual_run_id,
            "case_count": len(results),
            "passed": sum(1 for r in results if r.status == "pass"),
            "failed": sum(1 for r in results if r.status == "fail"),
            "at": now_iso,
        },
    )
    return (actual_run_id, new_ids)


@dataclass(frozen=True, slots=True)
class SentinelCaseTrend:
    case_name: str
    runs: int
    passes: int
    failures: int
    last_status: str
    last_seen_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "case_name": self.case_name,
            "runs": self.runs,
            "passes": self.passes,
            "failures": self.failures,
            "last_status": self.last_status,
            "last_seen_at": self.last_seen_at,
            "pass_rate": (self.passes / self.runs) if self.runs else 0.0,
        }


def sentinel_trends(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    window_days: int,
) -> list[SentinelCaseTrend]:
    """Aggregate per-case pass/fail rates over the recent window."""
    cutoff = (datetime.now(UTC) - timedelta(days=window_days)).isoformat()
    rows = conn.execute(
        """
        SELECT case_name,
               COUNT(*) AS runs,
               SUM(CASE WHEN status = 'pass' THEN 1 ELSE 0 END) AS passes,
               SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) AS failures,
               MAX(created_at) AS last_seen_at
        FROM retrieval_sentinel_results
        WHERE workspace_id = ? AND created_at >= ?
        GROUP BY case_name
        ORDER BY case_name
        """,
        (workspace_id, cutoff),
    ).fetchall()
    out: list[SentinelCaseTrend] = []
    for row in rows:
        case_name = str(row["case_name"])
        last_row = conn.execute(
            """
            SELECT status FROM retrieval_sentinel_results
            WHERE workspace_id = ? AND case_name = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (workspace_id, case_name),
        ).fetchone()
        last_status = str(last_row["status"]) if last_row else "unknown"
        out.append(
            SentinelCaseTrend(
                case_name=case_name,
                runs=int(row["runs"] or 0),
                passes=int(row["passes"] or 0),
                failures=int(row["failures"] or 0),
                last_status=last_status,
                last_seen_at=str(row["last_seen_at"] or ""),
            )
        )
    return out
