"""Crawl a workspace and ingest every non-excluded file.

python scripts/ingest_workspace.py --path /path/to/repo
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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

    provider = get_embedding_provider(settings)
    store = get_vector_store(settings)

    conn = open_connection(settings.db_path)
    try:
        apply_migrations(conn)
        ingested = 0
        skipped = 0
        for scanned in scan_workspace(args.path):
            try:
                content = scanned.absolute_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            language = _detect_language(scanned.absolute_path)
            result = ingest_file(
                conn,
                workspace_id=workspace_id,
                path=scanned.relative_path,
                content=content,
                language=language,
                embedding_provider=provider,
                vector_store=store,
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
        store.close()

    log.info("workspace_ingest_done", workspace_id=workspace_id, ingested=ingested, skipped=skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
