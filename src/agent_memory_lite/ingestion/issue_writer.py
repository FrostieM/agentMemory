"""Canonical issue writer: signature-based dedup on write (plan step 9.3).

An issue (bug / tech_debt / risk / error) is deduped by a normalized
signature (category + title) against existing OPEN issues, so the same
defect -- re-observed by repeated audits or error-fix episodes (step 9.4)
-- is not re-logged. A CLOSED issue (fixed / wontfix / accepted) with the
same signature does NOT block a new row: that is a recurrence worth
tracking as its own open issue.

Status transitions (open -> in_progress -> fixed / wontfix / accepted)
ride the generic ``storage.writer.edit`` path, which snapshots a version
into ``versions`` and appends an ``audit_log`` row -- so the lifecycle
already carries a full audit trail without bespoke code here.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.storage.reader import get_object
from agent_memory_lite.storage.writer import write as storage_write

# Only still-open issues block a duplicate. A signature match against a
# closed issue is a recurrence and gets its own fresh row.
_OPEN_STATUSES = ("open", "in_progress")


def issue_signature(title: str | None, category: str | None) -> str:
    """Stable dedup key: ``<category>|<whitespace-normalized lowercase title>``."""
    normalized_title = " ".join((title or "").lower().split())
    return f"{(category or 'bug').strip().lower()}|{normalized_title}"[:200]


def write_issue_canonical(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Write one issue, deduped against open issues with the same signature.

    Returns the existing issue's compact projection on a dedup hit, or the
    new row's projection otherwise.
    """
    title = str(payload.get("title") or "").strip()
    category = str(payload.get("category") or "bug")
    signature = str(payload.get("signature") or issue_signature(title, category))

    placeholders = ", ".join("?" for _ in _OPEN_STATUSES)
    existing = conn.execute(
        f"SELECT id FROM issues WHERE workspace_id = ? AND signature = ? "
        f"AND status IN ({placeholders}) ORDER BY created_at DESC LIMIT 1",
        (workspace_id, signature, *_OPEN_STATUSES),
    ).fetchone()
    if existing is not None:
        return get_object(conn, workspace_id=workspace_id, kind="issue", object_id=existing[0])

    body = {**payload, "signature": signature}
    return storage_write(conn, workspace_id=workspace_id, kind="issue", payload=body)
