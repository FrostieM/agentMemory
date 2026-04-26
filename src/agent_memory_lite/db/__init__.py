"""Database layer: connection, pragmas, transactions, migrations."""

from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import (
    MIGRATION_DIR,
    apply_migrations,
    discover_migrations,
)
from agent_memory_lite.db.pragmas import apply_pragmas
from agent_memory_lite.db.transactions import with_tx

__all__ = [
    "MIGRATION_DIR",
    "apply_migrations",
    "apply_pragmas",
    "close_connection",
    "discover_migrations",
    "open_connection",
    "with_tx",
]
