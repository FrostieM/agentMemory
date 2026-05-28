"""Freshness checks for project code memory."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from agent_memory_lite.cognition.codebase_scan import (
    DEFAULT_CODE_EXTENSIONS,
    DEFAULT_CODE_SKIP_DIRS,
    iter_source_files,
    source_file_sha1,
    stored_path,
)
from agent_memory_lite.maintenance.integrity_models import IntegrityCheck, table_exists


def _norm_path(path: str) -> str:
    return path.strip().replace("\\", "/").removeprefix("./")


def _row_value(row: sqlite3.Row | tuple[Any, ...], key: str, index: int) -> Any:
    if isinstance(row, sqlite3.Row):
        return row[key]
    return row[index]


def _digest_rows(conn: sqlite3.Connection, workspace_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT file_path, file_sha1, last_indexed_at, inbound_edge_count, symbol_count
        FROM code_digests
        WHERE workspace_id = ?
        """,
        (workspace_id,),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        path = _norm_path(str(_row_value(row, "file_path", 0)))
        out[path] = {
            "file_path": path,
            "file_sha1": _row_value(row, "file_sha1", 1),
            "last_indexed_at": _row_value(row, "last_indexed_at", 2),
            "inbound_edge_count": int(_row_value(row, "inbound_edge_count", 3) or 0),
            "symbol_count": int(_row_value(row, "symbol_count", 4) or 0),
        }
    return out


def code_memory_freshness_check(
    conn: sqlite3.Connection,
    workspace_id: str,
    *,
    project_root: Path | None,
    max_samples: int = 20,
) -> IntegrityCheck:
    """Detect source files whose code-memory digest is missing or content-stale."""
    if project_root is None:
        return IntegrityCheck(
            status="ok",
            details={"checked": False, "reason": "project_root_not_supplied"},
        )
    root = project_root.expanduser().resolve()
    if not root.is_dir():
        return IntegrityCheck(
            status="ok",
            details={
                "checked": False,
                "reason": "project_root_not_found",
                "project_root": str(root),
            },
        )
    if not table_exists(conn, "code_digests"):
        return IntegrityCheck(status="degraded", details={"reason": "code_digests_missing"})

    try:
        source_files = iter_source_files(
            root,
            extensions=DEFAULT_CODE_EXTENSIONS,
            skip_dirs=DEFAULT_CODE_SKIP_DIRS,
        )
    except OSError as exc:
        return IntegrityCheck(
            status="degraded",
            details={"checked": False, "reason": f"scan_failed: {exc}"},
        )

    digest_by_path = _digest_rows(conn, workspace_id)
    expected_relative: set[str] = set()
    expected_candidates: set[str] = set()
    missing: list[str] = []
    stale: list[dict[str, Any]] = []
    unreadable: list[dict[str, str]] = []

    for path in source_files:
        rel_path = stored_path(root, path, relative_paths=True)
        abs_path = _norm_path(str(path.resolve(strict=False)))
        expected_relative.add(rel_path)
        expected_candidates.update({rel_path, abs_path})
        digest = digest_by_path.get(rel_path) or digest_by_path.get(abs_path)
        if digest is None:
            missing.append(rel_path)
            continue
        try:
            current_sha1 = source_file_sha1(path)
        except OSError as exc:
            unreadable.append({"file_path": rel_path, "error": str(exc)})
            continue
        stored_sha1 = str(digest.get("file_sha1") or "")
        if not stored_sha1 or stored_sha1 != current_sha1:
            stale.append(
                {
                    "file_path": rel_path,
                    "stored_sha1": stored_sha1,
                    "current_sha1": current_sha1,
                    "last_indexed_at": digest.get("last_indexed_at") or "",
                    "inbound_edge_count": digest.get("inbound_edge_count") or 0,
                    "symbol_count": digest.get("symbol_count") or 0,
                }
            )

    orphaned = sorted(path for path in digest_by_path if path not in expected_candidates)
    missing_sorted = sorted(missing)
    stale_sorted = sorted(stale, key=lambda item: str(item["file_path"]))
    unreadable_sorted = sorted(unreadable, key=lambda item: item["file_path"])

    if stale_sorted or missing_sorted or unreadable_sorted:
        status = "degraded"
    elif orphaned:
        status = "warning"
    else:
        status = "ok"

    return IntegrityCheck(
        status=status,
        details={
            "checked": True,
            "project_root": str(root),
            "source_files": len(source_files),
            "digests": len(digest_by_path),
            "missing_digests": len(missing_sorted),
            "stale_digests": len(stale_sorted),
            "unreadable_files": len(unreadable_sorted),
            "orphaned_digests": len(orphaned),
            "missing_digests_sample": missing_sorted[:max_samples],
            "stale_digests_sample": stale_sorted[:max_samples],
            "unreadable_files_sample": unreadable_sorted[:max_samples],
            "orphaned_digests_sample": orphaned[:max_samples],
        },
    )
