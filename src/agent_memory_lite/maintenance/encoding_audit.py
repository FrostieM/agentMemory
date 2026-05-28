"""Audit and repair stored mojibake in memory text columns."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.maintenance.encoding_audit_models import (
    EncodingAuditReport,
    EncodingFinding,
)
from agent_memory_lite.maintenance.integrity import repair_fts
from agent_memory_lite.repositories.chunks_repo import mark_chunk_embedding_stale
from agent_memory_lite.utils.text_encoding import repair_common_mojibake

# Re-export so callers can keep `from ...encoding_audit import EncodingFinding`.
__all__ = [
    "TEXT_COLUMNS",
    "EncodingAuditReport",
    "EncodingFinding",
    "run_encoding_audit",
]

TEXT_COLUMNS: dict[str, tuple[str, ...]] = {
    "episodes": ("raw_text", "summary"),
    "chunks": ("text", "summary"),
    "tasks": ("goal", "next_action"),
    "decisions": ("title", "decision_text", "rationale"),
    "behaviors": ("name", "rule", "rationale"),
    "facts": ("literal_value", "fact_text"),
    "theories": ("title", "domain", "claim", "mechanism", "experiment_plan"),
    "theory_evidence": ("summary", "artifact_path"),
    "experiments": ("title", "hypothesis", "cohort_definition", "command", "owner"),
    "experiment_results": ("summary", "artifact_path"),
    "concepts": ("name", "kind", "definition"),
    "insights": ("summary", "proposed_action", "target_type"),
    "skills": ("name", "summary", "body_md"),
    "candidates": ("subject", "predicate", "object", "evidence"),
}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'virtual table')",
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _needs_repair(text: str) -> tuple[bool, str, str | None]:
    repaired = repair_common_mojibake(text)
    if repaired != text:
        return True, "repairable_mojibake", repaired
    replacement_count = text.count("�")
    if replacement_count >= 3:
        return True, "replacement_characters", None
    return False, "", None


def _sample(text: str) -> str:
    text = text.replace("\n", "\\n")
    return text[:180]


def run_encoding_audit(  # noqa: PLR0912
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    repair: bool = False,
    limit: int = 200,
) -> EncodingAuditReport:
    findings: list[EncodingFinding] = []
    scanned = 0
    repaired_cells = 0
    chunks_repaired = False

    for table, wanted_columns in TEXT_COLUMNS.items():
        columns = _columns(conn, table)
        if not columns or "workspace_id" not in columns or "id" not in columns:
            continue
        active_columns = [column for column in wanted_columns if column in columns]
        if not active_columns:
            continue
        selected = ", ".join(["id", *active_columns])
        rows = conn.execute(
            f"SELECT {selected} FROM {table} WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchall()
        for row in rows:
            for column in active_columns:
                value = row[column]
                if not isinstance(value, str) or not value:
                    continue
                scanned += 1
                needs_repair, issue, repaired = _needs_repair(value)
                if not needs_repair:
                    continue
                findings.append(
                    EncodingFinding(
                        table=table,
                        row_id=str(row["id"]),
                        column=column,
                        issue=issue,
                        before_sample=_sample(value),
                        after_sample=_sample(repaired) if repaired is not None else None,
                    )
                )
                if repair and repaired is not None:
                    conn.execute(
                        f"UPDATE {table} SET {column} = ? WHERE id = ? AND workspace_id = ?",
                        (repaired, row["id"], workspace_id),
                    )
                    repaired_cells += 1
                    if table == "chunks" and column == "text":
                        mark_chunk_embedding_stale(
                            conn,
                            chunk_id=str(row["id"]),
                            previous_text=value,
                            reason="encoding_repair_changed_chunk_text",
                        )
                    chunks_repaired = chunks_repaired or table == "chunks"
                if len(findings) >= limit and not repair:
                    break
            if len(findings) >= limit and not repair:
                break
        if len(findings) >= limit and not repair:
            break

    if repair and chunks_repaired:
        repair_fts(conn, workspace_id=workspace_id)

    status = "ok" if not findings or (repair and repaired_cells == len(findings)) else "warning"
    warnings: list[str] = []
    unresolved = len(findings) - repaired_cells
    if unresolved:
        warnings.append(f"{unresolved} encoding findings remain unresolved")
    return EncodingAuditReport(
        status=status,
        workspace_id=workspace_id,
        findings=findings,
        repaired_cells=repaired_cells,
        scanned_cells=scanned,
        warnings=warnings,
    )
