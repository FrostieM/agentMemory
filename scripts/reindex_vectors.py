"""Rebuild the chunks vector namespace from SQLite.

Run after changing the embedding model, or when LanceDB is corrupt:

    python scripts/reindex_vectors.py
"""

from __future__ import annotations

import sys

from agent_memory_lite.config.local_only_guard import (
    LocalOnlyError,
    assert_local_only,
)
from agent_memory_lite.config.settings import get_settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.embeddings.dimension_check import pin_or_check
from agent_memory_lite.embeddings.factory import get_embedding_provider
from agent_memory_lite.logging_setup import configure_logging, get_logger
from agent_memory_lite.vector_store.factory import get_vector_store
from agent_memory_lite.vector_store.reindex import reindex_chunks


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("reindex_vectors")

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
        pin_or_check(conn, settings.workspace_id, provider)
        total = reindex_chunks(
            conn,
            workspace_id=settings.workspace_id,
            provider=provider,
            store=store,
        )
    finally:
        close_connection(conn)
        store.close()

    log.info("reindex_complete", total=total, workspace_id=settings.workspace_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
