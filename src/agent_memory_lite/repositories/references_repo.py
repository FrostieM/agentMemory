"""Reverse-lookup: which memory rows mention a given id?

Powers ``POST /memory/what_references``: given a target id (decision,
theory, episode, role, …), find every memory row that names it in
its text fields. Useful for "all decisions that depend on this
theory" / "all chunks that mention this decision" / "every link that
targets this insight".

Implementation is intentionally simple: SQL ``LIKE`` against the
text columns of each table, plus an exact-id match against
``capability_links.target_id``. Limit the LIKE to the workspace so
cross-workspace mentions stay isolated.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# Per-table list of columns that may contain ad-hoc id references in
# text. Order matches priority in the response: ids show up earliest
# in the sources that matter most.
# v3.5 audit-followup: column names corrected to the actual schema.
# The audit caught ``tags`` and ``metadata`` here — those are stored as
# ``tags_json`` and ``metadata_json``. The wrong names raised
# ``sqlite3.DatabaseError``, which the per-table try/except below
# swallows silently, so what_references was returning incomplete
# results (theories + snapshots) without any error to the operator.
_REFERENCE_TABLES: list[tuple[str, str, list[str]]] = [
    ("decisions", "decision", ["title", "decision_text", "rationale", "supersedes_decision_id"]),
    ("theories", "theory", ["title", "claim", "mechanism", "experiment_plan", "tags_json"]),
    ("research_insights", "insight", ["summary", "proposed_action", "target_id"]),
    ("research_experiments", "experiment", ["title", "hypothesis", "theory_id", "snapshot_id"]),
    ("memory_snapshots", "snapshot", ["snapshot_key", "title", "metadata_json"]),
    ("chunks", "chunk", ["text", "summary"]),
    ("episodes", "episode", ["raw_text"]),
    ("behavior_instructions", "behavior_instruction", ["name", "rule", "rationale"]),
]


@dataclass(frozen=True, slots=True)
class ReferenceHit:
    table: str
    kind: str
    id: str
    label: str
    column: str


def _row_label(row: sqlite3.Row) -> str:
    for col in ("title", "name", "summary", "text", "raw_text", "claim"):
        try:
            value = row[col]
        except (IndexError, KeyError):
            continue
        if value:
            return str(value)[:120]
    return str(row["id"])[:120]


def _escape_like(value: str) -> str:
    """Escape SQL LIKE wildcards so a target_id containing ``%`` or
    ``_`` doesn't accidentally match unrelated rows. Pair with
    ``ESCAPE '\\'`` in the LIKE clause."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def find_references(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    target_id: str,
    limit: int = 50,
) -> list[ReferenceHit]:
    target_id = (target_id or "").strip()
    # Reject empty / unreasonably short ids so a stray empty string
    # never produces a global ``LIKE '%%'`` scan that could blow up
    # in a large workspace.
    if not target_id or len(target_id) < 3:
        return []
    pattern = f"%{_escape_like(target_id)}%"
    out: list[ReferenceHit] = []
    for table, kind, columns in _REFERENCE_TABLES:
        if len(out) >= limit:
            break
        where = " OR ".join(f"{col} LIKE ? ESCAPE '\\'" for col in columns)
        sql = f"SELECT * FROM {table} WHERE workspace_id = ? AND ({where}) LIMIT ?"
        params: list[object] = [workspace_id]
        params.extend(pattern for _ in columns)
        params.append(limit - len(out))
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.DatabaseError:
            continue
        for row in rows:
            out.append(
                ReferenceHit(
                    table=table,
                    kind=kind,
                    id=str(row["id"]),
                    label=_row_label(row),
                    column="text-match",
                )
            )
            if len(out) >= limit:
                break
    # Capability links are id-typed; match exact rather than LIKE.
    if len(out) < limit:
        try:
            link_rows = conn.execute(
                "SELECT id, target_type, target_id, capability_type, capability_id, capability_name "
                "FROM capability_links WHERE workspace_id = ? AND "
                "(target_id = ? OR capability_id = ?) LIMIT ?",
                (workspace_id, target_id, target_id, limit - len(out)),
            ).fetchall()
        except sqlite3.DatabaseError:
            link_rows = []
        for row in link_rows:
            out.append(
                ReferenceHit(
                    table="capability_links",
                    kind="capability_link",
                    id=str(row["id"]),
                    label=str(row["capability_name"] or row["capability_type"]),
                    column="capability_link.target_id"
                    if row["target_id"] == target_id
                    else "capability_link.capability_id",
                )
            )
    return out
