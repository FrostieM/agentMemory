"""Writer API — memory_write / memory_edit / memory_pin / memory_archive / memory_rollback.

Every mutation:
  1. Snapshots prior content into ``versions`` table (immutable history).
  2. Computes gist columns on write (no on-the-fly summarization at read).
  3. Appends an ``audit_log`` row tagged with ``agent_id``.
  4. Returns the compact projection of the resulting row.

The candidates table remains the correction-loop gate (v1.10) but is NOT
the hard gate for direct edits — rollback via versions covers that.

This module knows SQL. It does NOT depend on FastAPI, MCP, or any
agent runtime.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from typing import Any

from agent_memory_lite.storage.projections import project
from agent_memory_lite.storage.reader import get_object

# ============================================================
# Constants — kind → (table, gist_source_col, gist_target_col)
# ============================================================

_KIND_META: dict[str, tuple[str, str | None, str | None]] = {
    "decision": ("decisions", "decision_text", "gist"),
    "theory": ("theories", "claim", "gist"),
    "behavior": ("behaviors", "rule", "rule_one_line"),
    "skill": ("skills", "summary", "when_to_use_short"),
    "episode": ("episodes", "raw_text", "gist"),
    "concept": ("concepts", "definition", "definition_one_line"),
    "task": ("tasks", "goal", "goal_one_line"),
    "insight": ("insights", "summary", "gist"),
    "snapshot": ("snapshots", "title", None),
    "code_digest": ("code_digests", "narrative", "purpose_short"),
    "chunk": ("chunks", "text", "gist"),
    "plan_step": ("plan_steps", None, None),
}

# Columns that allow archiving via status='archived' vs is_archived=1.
_STATUS_ARCHIVE_KINDS = {"decision", "theory", "insight"}
_IS_ARCHIVED_KINDS = {"episode", "chunk", "files"}
_ACTIVE_FLAG_KINDS = {"behavior", "skill", "concept"}

# Kinds whose underlying tables have an updated_at column. schema
# omits updated_at on append-only kinds (episodes, chunks).
_HAS_UPDATED_AT = {
    "decision",
    "theory",
    "behavior",
    "skill",
    "concept",
    "task",
    "insight",
    "snapshot",
    "code_digest",
    "plan_step",
}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


_PREFIXES = {
    "decision": "dec",
    "theory": "th",
    "behavior": "beh",
    "skill": "skill",
    "episode": "ep",
    "concept": "concept",
    "task": "task",
    "insight": "insight",
    "snapshot": "snap",
    "code_digest": "digest",
    "chunk": "chk",
    "plan_step": "pstep",
}


def _compute_gist(payload: dict[str, Any], kind: str) -> str | None:
    """Heuristic gist: first sentence of the kind's main text field, ≤30 words."""
    meta = _KIND_META.get(kind)
    if meta is None:
        return None
    _, source_col, _ = meta
    if source_col is None:
        return None
    text = payload.get(source_col)
    if not isinstance(text, str) or not text.strip():
        return None
    # Find first sentence boundary or take whole.
    parts = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)
    first = parts[0] if parts else text.strip()
    words = first.split()
    if len(words) > 30:
        first = " ".join(words[:30]) + "..."
    return first


def _row_to_snapshot(row: sqlite3.Row | None) -> str:
    """Serialize a row to JSON string for versions table content_snapshot_json."""
    if row is None:
        return "{}"
    payload = {key: row[key] for key in dict(row)}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def _content_sha256(snapshot: str) -> str:
    return hashlib.sha256(snapshot.encode("utf-8")).hexdigest()


# ============================================================
# Versioning + audit helpers
# ============================================================


def _next_version_no(
    conn: sqlite3.Connection, *, workspace_id: str, kind: str, target_id: str
) -> int:
    """Return the next monotone version number for this target."""
    row = conn.execute(
        "SELECT COALESCE(MAX(version_no), 0) FROM versions "
        "WHERE workspace_id = ? AND target_kind = ? AND target_id = ?",
        (workspace_id, kind, target_id),
    ).fetchone()
    return int(row[0]) + 1


def _snapshot_version(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    target_id: str,
    row: sqlite3.Row | None,
    actor: str,
    why: str | None = None,
) -> int:
    """Insert one immutable version row. Returns the new version_no."""
    snapshot = _row_to_snapshot(row)
    sha = _content_sha256(snapshot)
    version_no = _next_version_no(conn, workspace_id=workspace_id, kind=kind, target_id=target_id)
    conn.execute(
        """INSERT INTO versions
           (id, workspace_id, target_kind, target_id, version_no,
            content_snapshot_json, content_sha256, actor, why, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _gen_id("ver"),
            workspace_id,
            kind,
            target_id,
            version_no,
            snapshot,
            sha,
            actor,
            why,
            _now_iso(),
        ),
    )
    return version_no


def _audit(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    action: str,
    kind: str,
    object_id: str,
    agent_id: str,
    before_row: sqlite3.Row | None,
    after_payload: dict[str, Any] | None,
    source_episode_id: str | None = None,
) -> None:
    """Append one audit_log row capturing the mutation."""
    before_json = _row_to_snapshot(before_row) if before_row is not None else None
    after_json = (
        json.dumps(after_payload, sort_keys=True, ensure_ascii=False, default=str)
        if after_payload is not None
        else None
    )
    conn.execute(
        """INSERT INTO audit_log
           (id, workspace_id, action, target_type, target_id,
            source_episode_id, agent_id, before_json, after_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _gen_id("audit"),
            workspace_id,
            action,
            kind,
            object_id,
            source_episode_id,
            agent_id,
            before_json,
            after_json,
            _now_iso(),
        ),
    )


# ============================================================
# memory_write — INSERT OR REPLACE with gist + version + audit
# ============================================================


_BI_TEMPORAL_WRITE_KINDS = {"decision", "theory", "concept", "behavior", "insight", "plan_step"}


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """PRAGMA-based column probe. Schema-aware writes use this to skip
    columns added by later migrations on a partially-migrated DB."""
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return False
    return any(row[1] == column for row in rows)


def _build_column_payload(
    conn: sqlite3.Connection,
    kind: str,
    payload: dict[str, Any],
    workspace_id: str,
    object_id: str,
) -> dict[str, Any]:
    """Ensure required columns are set; fill defaults; compute gist."""
    now = _now_iso()
    base: dict[str, Any] = {
        "id": object_id,
        "workspace_id": workspace_id,
        "created_at": payload.get("created_at") or now,
    }
    if kind in _HAS_UPDATED_AT:
        base["updated_at"] = now
    # Phase 6 bi-temporal: every kind that carries valid_from/valid_to
    # gets it defaulted to ``now`` on write so the as_of selector works.
    # Decisions had this from 0001_init; theory/concept/behavior/insight
    # got the columns in migration 0007 — probe the table so a pre-0007
    # DB (test fixtures, partial migration) doesn't crash on the unknown
    # column.
    if kind in _BI_TEMPORAL_WRITE_KINDS:
        table = _KIND_META[kind][0]
        if _table_has_column(conn, table, "valid_from"):
            base["valid_from"] = payload.get("valid_from") or now
    if kind == "code_digest":
        base["last_indexed_at"] = payload.get("last_indexed_at") or now
    base.update(payload)
    # Round-2 audit (CRITICAL): payload must not override the row's
    # identity or workspace — the function args are the source of
    # truth. Without this re-assert, payload={"workspace_id":"other"}
    # silently retargets the write into a foreign workspace.
    base["id"] = object_id
    base["workspace_id"] = workspace_id
    gist = _compute_gist(payload, kind)
    meta = _KIND_META[kind]
    _, _, gist_col = meta
    if gist_col and gist:
        base[gist_col] = base.get(gist_col) or gist
    return base


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Real column names of ``table`` — used to whitelist agent-supplied
    payload / fields keys before they are interpolated as SQL column
    names. ``table`` is always a hard-coded value from ``_KIND_META``,
    never user input, so the f-string here is safe."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def _insert_or_replace(conn: sqlite3.Connection, *, table: str, columns: dict[str, Any]) -> None:
    # Round-2 audit (HIGH): ``columns`` keys originate from the
    # agent-supplied WriteRequest.payload and were interpolated
    # straight into the INSERT column list. A key like
    # ``"id); DROP TABLE decisions;--"`` was a SQL-injection primitive;
    # a plain typo'd key raised a raw OperationalError that leaked SQL
    # text to the client. Whitelist against the table's real columns;
    # silently drop anything that is not a column.
    valid = _table_columns(conn, table)
    safe = {k: v for k, v in columns.items() if k in valid}
    if not safe:
        raise ValueError(f"payload has no column matching table {table!r}")
    cols = list(safe.keys())
    cols_csv = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT OR REPLACE INTO {table} ({cols_csv}) VALUES ({placeholders})"
    conn.execute(sql, [safe[c] for c in cols])


def write(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    payload: dict[str, Any],
    agent_id: str = "agent",
    source_episode_id: str | None = None,
) -> dict[str, Any] | None:
    """Upsert one row. Snapshots prior version + appends audit. Returns projection."""
    if kind not in _KIND_META:
        return None
    table, _, _ = _KIND_META[kind]
    object_id = payload.get("id") or _gen_id(_PREFIXES.get(kind, "obj"))
    # Read prior row for snapshot + audit.
    prior = conn.execute(
        f"SELECT * FROM {table} WHERE workspace_id = ? AND id = ?",
        (workspace_id, object_id),
    ).fetchone()
    if prior is not None:
        _snapshot_version(
            conn,
            workspace_id=workspace_id,
            kind=kind,
            target_id=object_id,
            row=prior,
            actor=agent_id,
        )
    columns = _build_column_payload(conn, kind, payload, workspace_id, object_id)
    _insert_or_replace(conn, table=table, columns=columns)
    _audit(
        conn,
        workspace_id=workspace_id,
        action="write" if prior is None else "update",
        kind=kind,
        object_id=object_id,
        agent_id=agent_id,
        before_row=prior,
        after_payload=columns,
        source_episode_id=source_episode_id,
    )
    conn.commit()
    new_row = conn.execute(
        f"SELECT * FROM {table} WHERE workspace_id = ? AND id = ?",
        (workspace_id, object_id),
    ).fetchone()
    return project(kind, new_row) if new_row is not None else None


# ============================================================
# memory_edit — partial update of specific fields
# ============================================================


def edit(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    object_id: str,
    fields: dict[str, Any],
    agent_id: str = "agent",
) -> dict[str, Any] | None:
    """Partial column update. Snapshots prior + audits. Returns projection."""
    if kind not in _KIND_META or not fields:
        return None
    table, _, _ = _KIND_META[kind]
    # Round-2 audit (HIGH): ``fields`` keys are interpolated into the
    # UPDATE SET clause below — the same SQL-column-injection vector as
    # _insert_or_replace. Whitelist against the table's real columns
    # before building the clause; drop unknown keys, and if nothing
    # valid remains treat it as a no-op edit.
    fields = {k: v for k, v in fields.items() if k in _table_columns(conn, table)}
    if not fields:
        return None
    prior = conn.execute(
        f"SELECT * FROM {table} WHERE workspace_id = ? AND id = ?",
        (workspace_id, object_id),
    ).fetchone()
    if prior is None:
        return None
    _snapshot_version(
        conn,
        workspace_id=workspace_id,
        kind=kind,
        target_id=object_id,
        row=prior,
        actor=agent_id,
    )
    set_clauses = ", ".join(f"{col} = ?" for col in fields)
    if kind in _HAS_UPDATED_AT:
        sql = f"UPDATE {table} SET {set_clauses}, updated_at = ? WHERE workspace_id = ? AND id = ?"
        params = [*fields.values(), _now_iso(), workspace_id, object_id]
    else:
        sql = f"UPDATE {table} SET {set_clauses} WHERE workspace_id = ? AND id = ?"
        params = [*fields.values(), workspace_id, object_id]
    conn.execute(sql, params)
    _audit(
        conn,
        workspace_id=workspace_id,
        action="edit",
        kind=kind,
        object_id=object_id,
        agent_id=agent_id,
        before_row=prior,
        after_payload=fields,
    )
    conn.commit()
    new_row = conn.execute(
        f"SELECT * FROM {table} WHERE workspace_id = ? AND id = ?",
        (workspace_id, object_id),
    ).fetchone()
    return project(kind, new_row) if new_row is not None else None


# ============================================================
# memory_pin — flip pinned flag (decisions + behaviors only)
# ============================================================


def pin(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    object_id: str,
    pinned: bool,
    agent_id: str = "agent",
) -> dict[str, Any] | None:
    """Set pinned column. Valid only for kinds with a ``pinned`` column."""
    if kind not in {"decision", "behavior"}:
        return None
    return edit(
        conn,
        workspace_id=workspace_id,
        kind=kind,
        object_id=object_id,
        fields={"pinned": 1 if pinned else 0},
        agent_id=agent_id,
    )


# ============================================================
# memory_archive — soft-delete (kind-specific archive semantics)
# ============================================================


def archive(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    object_id: str,
    reason: str | None = None,
    agent_id: str = "agent",
) -> dict[str, Any] | None:
    """Soft-delete using the kind's archive convention.

    * decisions / theories / insights → status='archived'.
    * episodes / chunks / files → is_archived=1.
    * behaviors / skills / concepts → active=0.
    """
    fields: dict[str, Any]
    if kind in _STATUS_ARCHIVE_KINDS:
        fields = {"status": "archived"}
    elif kind in _IS_ARCHIVED_KINDS:
        fields = {"is_archived": 1}
    elif kind in _ACTIVE_FLAG_KINDS:
        fields = {"active": 0}
    else:
        return None
    out = edit(
        conn,
        workspace_id=workspace_id,
        kind=kind,
        object_id=object_id,
        fields=fields,
        agent_id=agent_id,
    )
    if reason and out:
        _audit(
            conn,
            workspace_id=workspace_id,
            action="archive_reason",
            kind=kind,
            object_id=object_id,
            agent_id=agent_id,
            before_row=None,
            after_payload={"reason": reason},
        )
        conn.commit()
    return out


# ============================================================
# memory_rollback — restore historical version as new version
# ============================================================


def rollback(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    object_id: str,
    to_version: int,
    agent_id: str = "agent",
    why: str,
) -> dict[str, Any] | None:
    """Restore the content of ``version_no=to_version`` as a NEW version.

    Forward-only history: rollback writes historical content as a new
    version_no (max + 1). No prior versions are ever deleted.
    """
    if not why:
        return None
    if kind not in _KIND_META:
        return None
    table, _, _ = _KIND_META[kind]
    target = conn.execute(
        """SELECT content_snapshot_json FROM versions
           WHERE workspace_id = ? AND target_kind = ? AND target_id = ? AND version_no = ?""",
        (workspace_id, kind, object_id, to_version),
    ).fetchone()
    if target is None:
        return None
    try:
        snapshot = json.loads(target[0])
    except (TypeError, ValueError):
        return None
    if not isinstance(snapshot, dict):
        return None
    # Snapshot the CURRENT live row as a new version BEFORE we overwrite.
    current = conn.execute(
        f"SELECT * FROM {table} WHERE workspace_id = ? AND id = ?",
        (workspace_id, object_id),
    ).fetchone()
    _snapshot_version(
        conn,
        workspace_id=workspace_id,
        kind=kind,
        target_id=object_id,
        row=current,
        actor=agent_id,
        why=f"pre-rollback to v{to_version}",
    )
    snapshot["workspace_id"] = workspace_id
    snapshot["id"] = object_id
    snapshot["updated_at"] = _now_iso()
    _insert_or_replace(conn, table=table, columns=snapshot)
    _audit(
        conn,
        workspace_id=workspace_id,
        action="rollback",
        kind=kind,
        object_id=object_id,
        agent_id=agent_id,
        before_row=current,
        after_payload={"to_version": to_version, "why": why},
    )
    conn.commit()
    new_row = conn.execute(
        f"SELECT * FROM {table} WHERE workspace_id = ? AND id = ?",
        (workspace_id, object_id),
    ).fetchone()
    return project(kind, new_row) if new_row is not None else None


# ============================================================
# Helpers exported for tests + callers
# ============================================================


def list_versions(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    object_id: str,
) -> list[dict[str, Any]]:
    """Return all versions for a target, newest first."""
    rows = conn.execute(
        """SELECT version_no, actor, why, created_at, content_sha256 FROM versions
           WHERE workspace_id = ? AND target_kind = ? AND target_id = ?
           ORDER BY version_no DESC""",
        (workspace_id, kind, object_id),
    ).fetchall()
    return [dict(r) for r in rows]


__all__ = [
    "archive",
    "edit",
    "list_versions",
    "pin",
    "rollback",
    "write",
]


# Re-import get_object for downstream convenience.
_ = get_object
