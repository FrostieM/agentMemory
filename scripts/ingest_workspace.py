"""Crawl a workspace and ingest every non-excluded file.

python scripts/ingest_workspace.py --path /path/to/repo
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from agent_memory_lite.config.local_only_guard import (
    LocalOnlyError,
    assert_local_only,
)
from agent_memory_lite.config.settings import get_settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.embeddings.factory import get_embedding_provider
from agent_memory_lite.ingestion.file_pipeline import ingest_file
from agent_memory_lite.ingestion.workspace_scanner import scan_workspace
from agent_memory_lite.logging_setup import configure_logging, get_logger
from agent_memory_lite.vector_store.factory import get_vector_store


def _delete_vectors(vector_store: Any, chunk_ids: list[str]) -> int:
    deleted = 0
    for i in range(0, len(chunk_ids), 500):
        deleted += int(vector_store.delete("chunks", chunk_ids[i : i + 500]))
    return deleted


def prune_stale_files(
    conn,
    *,
    workspace_id: str,
    expected_paths: set[str],
    vector_store: Any | None,
) -> dict[str, int]:
    conn.execute("DROP TABLE IF EXISTS temp._ingest_expected_paths")
    conn.execute("DROP TABLE IF EXISTS temp._ingest_stale_file_ids")
    conn.execute("DROP TABLE IF EXISTS temp._ingest_stale_chunk_ids")
    conn.execute("CREATE TEMP TABLE _ingest_expected_paths (path TEXT PRIMARY KEY)")
    conn.executemany(
        "INSERT INTO _ingest_expected_paths (path) VALUES (?)",
        [(path,) for path in sorted(expected_paths)],
    )
    conn.execute(
        """
        CREATE TEMP TABLE _ingest_stale_file_ids AS
        SELECT id FROM files
        WHERE workspace_id = ?
          AND path NOT IN (SELECT path FROM _ingest_expected_paths)
        """,
        (workspace_id,),
    )
    conn.execute(
        """
        CREATE TEMP TABLE _ingest_stale_chunk_ids AS
        SELECT id FROM chunks
        WHERE workspace_id = ?
          AND file_id IN (SELECT id FROM _ingest_stale_file_ids)
        """,
        (workspace_id,),
    )
    chunk_ids = [
        str(row[0]) for row in conn.execute("SELECT id FROM _ingest_stale_chunk_ids").fetchall()
    ]
    files = int(conn.execute("SELECT COUNT(*) FROM _ingest_stale_file_ids").fetchone()[0] or 0)
    edges = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM symbol_edges
            WHERE workspace_id = ?
              AND (src_chunk_id IN (SELECT id FROM _ingest_stale_chunk_ids)
                   OR dst_chunk_id IN (SELECT id FROM _ingest_stale_chunk_ids))
            """,
            (workspace_id,),
        ).fetchone()[0]
        or 0
    )
    vectors = (
        _delete_vectors(vector_store, chunk_ids) if vector_store is not None and chunk_ids else 0
    )
    conn.execute(
        """
        DELETE FROM symbol_edges
        WHERE workspace_id = ?
          AND (src_chunk_id IN (SELECT id FROM _ingest_stale_chunk_ids)
               OR dst_chunk_id IN (SELECT id FROM _ingest_stale_chunk_ids))
        """,
        (workspace_id,),
    )
    conn.execute(
        """
        DELETE FROM chunks_fts
        WHERE workspace_id = ?
          AND chunk_id IN (SELECT id FROM _ingest_stale_chunk_ids)
        """,
        (workspace_id,),
    )
    conn.execute(
        """
        DELETE FROM chunks
        WHERE workspace_id = ?
          AND id IN (SELECT id FROM _ingest_stale_chunk_ids)
        """,
        (workspace_id,),
    )
    conn.execute(
        """
        DELETE FROM files
        WHERE workspace_id = ?
          AND id IN (SELECT id FROM _ingest_stale_file_ids)
        """,
        (workspace_id,),
    )
    conn.execute("DROP TABLE IF EXISTS temp._ingest_expected_paths")
    conn.execute("DROP TABLE IF EXISTS temp._ingest_stale_file_ids")
    conn.execute("DROP TABLE IF EXISTS temp._ingest_stale_chunk_ids")
    conn.commit()
    return {"files": files, "chunks": len(chunk_ids), "edges": edges, "vectors": vectors}


LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".kt": "kotlin",
    ".md": "markdown",
    ".markdown": "markdown",
    ".rst": "markdown",
}


def _detect_language(path: Path) -> str | None:
    return LANGUAGE_BY_SUFFIX.get(path.suffix.lower())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest a workspace into agent-memory-lite.")
    parser.add_argument("--workspace", default=None, help="workspace_id (defaults to settings)")
    parser.add_argument("--path", required=True, help="Workspace root path.")
    parser.add_argument(
        "--prune-stale",
        action="store_true",
        help="Delete file/chunk/vector rows for files excluded from the current scan.",
    )
    parser.add_argument(
        "--no-vectors",
        action="store_true",
        help="Update SQLite + FTS only; rebuild vectors later with memory_audit.",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    workspace_id = args.workspace or settings.workspace_id
    configure_logging(settings.log_level)
    log = get_logger("ingest_workspace")

    try:
        assert_local_only(settings)
    except LocalOnlyError as exc:
        log.error("local_only_violation", error=str(exc))
        return 2

    provider = None if args.no_vectors else get_embedding_provider(settings)
    store = get_vector_store(settings) if args.prune_stale or not args.no_vectors else None

    conn = open_connection(settings.db_path)
    try:
        apply_migrations(conn)
        ingested = 0
        skipped = 0
        scanned_files = list(scan_workspace(args.path))
        if args.prune_stale:
            counts = prune_stale_files(
                conn,
                workspace_id=workspace_id,
                expected_paths={scanned.relative_path for scanned in scanned_files},
                vector_store=store,
            )
            log.info("stale_files_pruned", **counts)
        for scanned in scanned_files:
            try:
                source_bytes = scanned.absolute_path.read_bytes()
                content = source_bytes.decode("utf-8")
            except UnicodeDecodeError:
                continue
            language = _detect_language(scanned.absolute_path)
            result = ingest_file(
                conn,
                workspace_id=workspace_id,
                path=scanned.relative_path,
                content=content,
                source_bytes=source_bytes,
                language=language,
                embedding_provider=provider,
                vector_store=store if not args.no_vectors else None,
            )
            if result.skipped:
                skipped += 1
            else:
                ingested += 1
                log.info(
                    "file_ingested",
                    path=scanned.relative_path,
                    chunks=result.chunks_written,
                )
    finally:
        close_connection(conn)
        if store is not None:
            store.close()

    log.info("workspace_ingest_done", workspace_id=workspace_id, ingested=ingested, skipped=skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
