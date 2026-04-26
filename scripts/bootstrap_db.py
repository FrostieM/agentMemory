"""Apply all pending migrations against the configured database.

Run this once after installing the package (or whenever new migrations land):

    python scripts/bootstrap_db.py
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
from agent_memory_lite.logging_setup import configure_logging, get_logger


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("bootstrap_db")

    try:
        assert_local_only(settings)
    except LocalOnlyError as exc:
        log.error("local_only_violation", error=str(exc))
        return 2

    conn = open_connection(settings.db_path)
    try:
        applied = apply_migrations(conn)
    finally:
        close_connection(conn)

    if applied:
        log.info("migrations_applied", versions=applied, db_path=str(settings.db_path))
    else:
        log.info("migrations_up_to_date", db_path=str(settings.db_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
