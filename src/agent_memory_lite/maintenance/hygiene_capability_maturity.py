"""Find capabilities that have not been invoked in too long.

Surfaced by `/memory/hygiene_report` when the v1.5 maturity flag is on.
A capability stale by `last_invoked_at` is not a defect — it may simply
be quarterly. The finding is `severity="info"` so the operator sees it
without it counting against retrieval integrity. Pinned capabilities
are excluded (operator-anchored).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from agent_memory_lite.maintenance.hygiene_models import HygieneFinding

CAPABILITY_SUBTYPES: tuple[str, ...] = ("skill", "role", "playbook")


def find_stale_capabilities(
    conn: sqlite3.Connection, *, workspace_id: str, stale_days: int
) -> list[HygieneFinding]:
    """Emit `stale_capability` findings for active capabilities older than
    `stale_days` since their `last_invoked_at`. Capabilities that have
    `last_invoked_at IS NULL` are skipped — the system has no evidence
    they are stale yet."""
    cutoff = (datetime.now(UTC) - timedelta(days=stale_days)).isoformat()
    findings: list[HygieneFinding] = []
    for kind in CAPABILITY_SUBTYPES:
        rows = conn.execute(
            """
            SELECT id, name, last_invoked_at, usage_count, success_count, failure_count
            FROM skills
            WHERE workspace_id = ?
              AND subtype = ?
              AND active = 1
              AND last_invoked_at IS NOT NULL
              AND last_invoked_at < ?
            ORDER BY last_invoked_at
            """,
            (workspace_id, kind, cutoff),
        ).fetchall()
        for row in rows:
            findings.append(
                HygieneFinding(
                    kind="stale_capability",
                    severity="info",
                    target_type=kind,
                    target_id=str(row["id"]),
                    summary=(
                        f"Capability not invoked in over {stale_days} days; "
                        "review whether it still matches a real workflow."
                    ),
                    details={
                        "name": str(row["name"]),
                        "last_invoked_at": str(row["last_invoked_at"]),
                        "usage_count": int(row["usage_count"] or 0),
                        "success_count": int(row["success_count"] or 0),
                        "failure_count": int(row["failure_count"] or 0),
                    },
                )
            )
    return findings
