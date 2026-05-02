"""``memory_archive`` dispatcher.

One callable that flips the right "soft-delete" axis for whatever
memory kind the caller names. The HTTP route + MCP tool wrap this; the
goal is the agent doesn't have to remember whether it's
``status='archived'`` for theories vs ``active=0`` for behavior
instructions vs ``status='superseded'`` for decisions.

Supported kinds:

* ``chunk`` / ``episode`` / ``file``     - is_archived flag (added by
                                            migration 0016)
* ``decision``                            - status=superseded, valid_to=now
* ``theory``                              - status=archived
* ``insight``                             - status=archived
* ``role`` / ``skill`` / ``playbook``     - active=0
* ``behavior_instruction``                - active=0
* ``candidate``                           - reuse reject/promote pipeline

Status-axis kinds (decision / theory / insight) preserve the
pre-archive status in audit_log so restore lands on the original
value instead of a generic default. See
``archive_status_kinds.py``.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.archive_status_kinds import (
    STATUS_KIND_MAP,
    archive_status_kind,
)
from agent_memory_lite.ingestion.candidate_writer import (
    promote_memory_candidate,
    reject_memory_candidate,
)
from agent_memory_lite.repositories.archive_repo import set_text_row_archived
from agent_memory_lite.utils.time import iso_now

_ACTIVE_KIND_MAP: dict[str, str] = {
    "role": "agent_roles",
    "skill": "agent_skills",
    "playbook": "agent_playbooks",
    "behavior_instruction": "behavior_instructions",
}


def _result(kind: str, object_id: str, archive: bool, found: bool) -> dict[str, object]:
    return {"kind": kind, "id": object_id, "archived": archive, "found": found}


def _archive_active_kind(
    conn: sqlite3.Connection,
    *,
    kind: str,
    workspace_id: str,
    object_id: str,
    archive: bool,
) -> dict[str, object]:
    table = _ACTIVE_KIND_MAP[kind]
    cur = conn.execute(
        f"UPDATE {table} SET active = ?, updated_at = ? WHERE id = ? AND workspace_id = ?",
        (0 if archive else 1, iso_now(), object_id, workspace_id),
    )
    conn.commit()
    return _result(kind, object_id, archive, cur.rowcount > 0)


def archive_memory_object(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    object_id: str,
    archive: bool = True,
) -> dict[str, object]:
    """Archive (or restore) one memory object across kinds."""
    kind = (kind or "").lower().strip()
    if kind in {"chunk", "episode", "file"}:
        ok = set_text_row_archived(
            conn,
            kind=kind,
            workspace_id=workspace_id,
            object_id=object_id,
            archived=archive,
        )
        return _result(kind, object_id, archive, ok)
    if kind in STATUS_KIND_MAP:
        found, _prior = archive_status_kind(
            conn,
            kind=kind,
            workspace_id=workspace_id,
            object_id=object_id,
            archive=archive,
        )
        return _result(kind, object_id, archive, found)
    if kind in _ACTIVE_KIND_MAP:
        return _archive_active_kind(
            conn, kind=kind, workspace_id=workspace_id, object_id=object_id, archive=archive
        )
    if kind == "candidate":
        if archive:
            cand = reject_memory_candidate(conn, candidate_id=object_id)
        else:
            cand = promote_memory_candidate(conn, candidate_id=object_id)
        return _result(kind, object_id, archive, cand is not None)
    raise ValueError(f"unsupported archive kind: {kind!r}")
