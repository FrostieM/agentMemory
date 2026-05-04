"""Explicit transaction context manager.

Connections are opened in autocommit mode (`isolation_level=None`) so callers
must use `with_tx(conn)` to scope a transaction. This makes the boundary obvious
in code and keeps tests deterministic.

Composability (added in 1.2.1): when ``with_tx`` is entered while an outer
transaction is already open, it uses a SAVEPOINT instead of nested BEGIN
(which SQLite forbids). This lets an orchestrator wrap several helpers — each
of which uses its own ``with_tx`` — in a single all-or-nothing block:

    with with_tx(conn):
        upsert_behavior_instruction(conn, ...)   # uses savepoint internally
        pin_memory_object(conn, ...)             # ditto
        # candidate flip + audit; ROLLBACK here undoes everything above

Any inner failure rolls back to the savepoint; the outer transaction commits
or rolls back as one unit. Pre-1.2.1 callers that nested ``with_tx`` raised
``OperationalError: cannot start a transaction within a transaction`` — that
path is now safe.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def with_tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    if conn.in_transaction:
        # SAVEPOINT name must be a valid identifier; hex-only token suffices
        # and avoids quoting concerns. Per SQLite docs, RELEASE finalizes the
        # savepoint into the outer transaction; ROLLBACK TO + RELEASE undoes
        # the savepoint's writes without aborting the outer transaction.
        # 8 bytes = ~1.8e19 namespace, collision-safe even for deeply nested
        # batch jobs. RELEASE is wrapped in finally + suppress so the
        # original exception is preserved if the rollback path itself fails.
        import contextlib  # noqa: PLC0415

        name = "tx_" + secrets.token_hex(8)
        conn.execute(f"SAVEPOINT {name}")
        try:
            try:
                yield conn
            except BaseException:
                with contextlib.suppress(sqlite3.Error):
                    conn.execute(f"ROLLBACK TO {name}")
                raise
        finally:
            with contextlib.suppress(sqlite3.Error):
                conn.execute(f"RELEASE {name}")
        return
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
