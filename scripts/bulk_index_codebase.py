"""Bulk-index a project's codebase into the v3 ``code_digests`` table.

Without this, ``memory_impact_check`` returns ``verdict='not_indexed'``
for every file — the discipline primitive can't do its job until there
is data to query.  The PostToolUse hook populates digests incrementally
as the agent edits files, but a fresh deployment needs a one-shot
bootstrap to cover the rest of the project.

This script walks the project tree, computes a heuristic digest for
each source file (via ``v3.cognition.digest_worker.compute_digest``),
and UPSERTs into ``code_digests``.  Idempotent — re-running updates
``last_indexed_at`` + ``file_sha1`` and skips files whose SHA already
matches.

Skips by default:  ``.venv`` / ``__pycache__`` / ``.git`` /
``node_modules`` / ``.agent_memory`` / ``dist`` / ``build`` /
``.pytest_cache`` / ``.ruff_cache``.

Usage::

    python scripts/bulk_index_codebase.py \\
        --project C:\\path\\to\\repo \\
        --workspace <workspace_id> \\
        --db-path C:\\path\\to\\.agent_memory\\memory.db

    # Print summary as JSON for CI:
    python scripts/bulk_index_codebase.py --project ... --workspace ... \\
        --db-path ... --json

    # Force re-index (ignore SHA match):
    python scripts/bulk_index_codebase.py ... --force
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# Force UTF-8 stdout so summary glyphs survive Windows cp1251.
with contextlib.suppress(AttributeError, ValueError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
with contextlib.suppress(AttributeError, ValueError):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

# Make the agent_memory_lite package importable when run as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent_memory_lite.cognition.code_indexer import (  # noqa: E402
    index_file,
    refresh_digest_edge_counts,
    resolve_all_pending_edges,
)
from agent_memory_lite.cognition.digest_worker import (  # noqa: E402
    compute_digest,
    upsert_digest,
)

# File extensions we consider source code worth digesting. Aligned with
# the digest_worker._detect_language mapping — anything that returns a
# language is indexable.
DEFAULT_EXTENSIONS = frozenset(
    {".py", ".ts", ".tsx", ".js", ".jsx", ".sql", ".md", ".yml", ".yaml", ".toml", ".ps1", ".sh"}
)

# Directories we never descend into.
DEFAULT_SKIP_DIRS = frozenset(
    {
        ".venv",
        "venv",
        "__pycache__",
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".agent_memory",
        "dist",
        "build",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "htmlcov",
        ".tox",
        ".idea",
        ".vscode",
        # Transient / generated artifacts that shouldn't enter the
        # workspace's code graph — Playwright screenshot dumps include
        # bundled Chrome extension cache scripts that dominate the
        # caller-count ranking with thousands of synthetic edges.
        "tmp-ui-shots",
        "tmp",
        "coverage",
    }
)


# ============================================================
# Data model
# ============================================================


@dataclass(slots=True)
class IndexReport:
    """Per-run summary returned to stdout / JSON."""

    project: str
    workspace_id: str
    db_path: str
    walked: int = 0
    indexed: int = 0
    skipped_unchanged: int = 0
    skipped_unindexable: int = 0
    errors: int = 0
    chunks: int = 0
    edges_first_pass: int = 0
    edges_resolved_second_pass: int = 0
    digests_edge_refreshed: int = 0
    languages: Counter[str] = field(default_factory=Counter)
    error_details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "project": self.project,
            "workspace_id": self.workspace_id,
            "db_path": self.db_path,
            "walked": self.walked,
            "indexed": self.indexed,
            "skipped_unchanged": self.skipped_unchanged,
            "skipped_unindexable": self.skipped_unindexable,
            "errors": self.errors,
            "chunks": self.chunks,
            "edges_first_pass": self.edges_first_pass,
            "edges_resolved_second_pass": self.edges_resolved_second_pass,
            "digests_edge_refreshed": self.digests_edge_refreshed,
            "languages": dict(self.languages),
            "error_details": list(self.error_details)[:20],
        }


# ============================================================
# Walking + indexing
# ============================================================


def iter_source_files(
    project_root: Path,
    *,
    extensions: frozenset[str] = DEFAULT_EXTENSIONS,
    skip_dirs: frozenset[str] = DEFAULT_SKIP_DIRS,
) -> list[Path]:
    """Walk ``project_root`` and return every file with an indexable extension."""
    files: list[Path] = []
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        # Skip any path containing a skip-dir segment.
        try:
            rel_parts = path.relative_to(project_root).parts
        except ValueError:
            continue
        if any(part in skip_dirs for part in rel_parts):
            continue
        if path.suffix.lower() in extensions:
            files.append(path)
    return sorted(files)


def _existing_sha(conn: sqlite3.Connection, *, workspace_id: str, file_path: str) -> str | None:
    row = conn.execute(
        "SELECT file_sha1 FROM code_digests WHERE workspace_id = ? AND file_path = ?",
        (workspace_id, file_path),
    ).fetchone()
    return row[0] if row else None


def bulk_index(  # noqa: PLR0912, PLR0915 — linear walk with explicit branches per per-file outcome
    project_root: Path,
    *,
    workspace_id: str,
    db_path: Path,
    force: bool = False,
    extensions: frozenset[str] = DEFAULT_EXTENSIONS,
    skip_dirs: frozenset[str] = DEFAULT_SKIP_DIRS,
    relative_paths: bool = True,
    with_edges: bool = True,
) -> IndexReport:
    """Walk ``project_root`` and index each source file via ``index_file``.

    With ``with_edges=True`` (default) the indexer populates the full
    chunks + symbol_edges + code_digests trio so ``memory_impact_check``
    returns differentiated verdicts.  With ``with_edges=False`` only the
    ``code_digests`` row is upserted (faster, but ``impact_check`` will
    return ``verdict='low'`` for every file).

    ``relative_paths=True`` stores ``file_path`` as project-relative
    (``src/foo.py``) so digests survive moving the project root.
    """
    report = IndexReport(
        project=str(project_root),
        workspace_id=workspace_id,
        db_path=str(db_path),
    )
    if not db_path.exists():
        report.error_details.append(f"db not found: {db_path}")
        report.errors += 1
        return report
    files = iter_source_files(project_root, extensions=extensions, skip_dirs=skip_dirs)
    conn = sqlite3.connect(db_path)
    try:
        for file_path in files:
            report.walked += 1
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                report.errors += 1
                report.error_details.append(f"read {file_path}: {exc}")
                continue
            digest_preview = compute_digest(str(file_path), content)
            if digest_preview.language is None:
                report.skipped_unindexable += 1
                continue
            stored_path = (
                str(file_path.relative_to(project_root)).replace("\\", "/")
                if relative_paths
                else str(file_path)
            )
            if with_edges:
                result = index_file(
                    conn,
                    workspace_id=workspace_id,
                    rel_path=stored_path,
                    content=content,
                    language=digest_preview.language,
                    force=force,
                )
                if result.error:
                    report.errors += 1
                    report.error_details.append(f"index {stored_path}: {result.error}")
                    continue
                if result.skipped_unchanged:
                    report.skipped_unchanged += 1
                    continue
                report.indexed += 1
                report.chunks += result.chunks
                report.edges_first_pass += result.edges
                report.languages[digest_preview.language or "unknown"] += 1
                conn.commit()
                continue
            # with_edges=False: digest-only legacy path
            if not force:
                existing = _existing_sha(conn, workspace_id=workspace_id, file_path=stored_path)
                if existing == digest_preview.file_sha1:
                    report.skipped_unchanged += 1
                    continue
            try:
                portable = compute_digest(stored_path, content)
                upsert_digest(conn, workspace_id=workspace_id, result=portable)
                report.indexed += 1
                report.languages[portable.language or "unknown"] += 1
            except sqlite3.Error as exc:
                report.errors += 1
                report.error_details.append(f"upsert {stored_path}: {exc}")
        # Cross-file resolver: stitch up edges whose targets appeared in
        # files indexed AFTER the source.  Then refresh digest counts so
        # impact_check verdicts reflect the final graph.
        if with_edges:
            try:
                report.edges_resolved_second_pass = resolve_all_pending_edges(
                    conn, workspace_id=workspace_id
                )
                report.digests_edge_refreshed = refresh_digest_edge_counts(
                    conn, workspace_id=workspace_id
                )
                conn.commit()
            except sqlite3.Error as exc:
                report.errors += 1
                report.error_details.append(f"cross-file resolve: {exc}")
    finally:
        conn.close()
    return report


# ============================================================
# CLI
# ============================================================


def render_human(report: IndexReport) -> str:
    lines = [
        f"# Bulk index report — workspace: {report.workspace_id}",
        f"project: {report.project}",
        f"db:      {report.db_path}",
        "",
        f"  walked            = {report.walked}",
        f"  indexed           = {report.indexed}",
        f"  skipped_unchanged = {report.skipped_unchanged}",
        f"  skipped_unindexable = {report.skipped_unindexable}",
        f"  errors            = {report.errors}",
        "",
        f"  chunks            = {report.chunks}",
        f"  edges (1st pass)  = {report.edges_first_pass}",
        f"  edges (2nd pass)  = {report.edges_resolved_second_pass}",
        f"  digests refreshed = {report.digests_edge_refreshed}",
    ]
    if report.languages:
        lines.append("")
        lines.append("  by language:")
        for lang, n in sorted(report.languages.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {lang:12} = {n}")
    if report.error_details:
        lines.append("")
        lines.append("  first error details:")
        for detail in report.error_details[:5]:
            lines.append(f"    - {detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bulk-index a project's codebase into the v3 code_digests table."
    )
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--db-path", required=True, type=Path)
    parser.add_argument(
        "--extensions",
        nargs="+",
        default=sorted(DEFAULT_EXTENSIONS),
        help=f"File extensions to index. Default: {sorted(DEFAULT_EXTENSIONS)}",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-index every file regardless of unchanged file_sha1.",
    )
    parser.add_argument(
        "--absolute-paths",
        action="store_true",
        help="Store absolute file_path (default: relative to --project).",
    )
    parser.add_argument(
        "--no-edges",
        action="store_true",
        help=(
            "Skip chunks + symbol_edges extraction.  Only writes code_digests"
            " rows (faster but impact_check verdicts stay at 'low')."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human render.")
    args = parser.parse_args(argv)

    project_root = args.project.resolve()
    if not project_root.exists():
        sys.stderr.write(f"project not found: {project_root}\n")
        return 2
    db_path = args.db_path.resolve()
    extensions = frozenset(ext if ext.startswith(".") else f".{ext}" for ext in args.extensions)

    report = bulk_index(
        project_root,
        workspace_id=args.workspace,
        db_path=db_path,
        force=args.force,
        extensions=extensions,
        relative_paths=not args.absolute_paths,
        with_edges=not args.no_edges,
    )

    if args.json:
        sys.stdout.write(json.dumps(report.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(render_human(report) + "\n")
    return 0 if report.errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
