"""Run the eval harness against a temp DB and print + persist a JSON report.

Each eval case runs against a fresh in-memory SQLite database so cases stay
isolated. Use --no-vector for a fast FTS-only run that does not load an
embedding model or vector store. Standalone use:

    python scripts/run_evals.py --workspace default --no-vector
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agent_memory_lite.config.local_only_guard import (
    LocalOnlyError,
    assert_local_only,
)
from agent_memory_lite.config.settings import get_settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.embeddings.factory import get_embedding_provider
from agent_memory_lite.evals.reporters import to_console, to_json, write_report
from agent_memory_lite.evals.runner import run_evals
from agent_memory_lite.logging_setup import configure_logging
from agent_memory_lite.vector_store.factory import get_vector_store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run agent-memory-lite evals.")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--output", default=None, help="Write JSON report here.")
    parser.add_argument(
        "--no-vector",
        action="store_true",
        help="Run with SQLite FTS only; no embedding model or vector store is loaded.",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        assert_local_only(settings)
    except LocalOnlyError as exc:
        print(f"local_only_violation: {exc}", file=sys.stderr)
        return 2

    provider = None if args.no_vector else get_embedding_provider(settings)
    store = None if args.no_vector else get_vector_store(settings)

    @contextmanager
    def _conn_factory() -> Iterator[sqlite3.Connection]:
        conn = open_connection(":memory:")
        try:
            apply_migrations(conn)
            yield conn
        finally:
            close_connection(conn)

    try:
        report = run_evals(
            _conn_factory,
            workspace_id=args.workspace or settings.workspace_id,
            embedding_provider=provider,
            vector_store=store,
        )
    finally:
        if store is not None:
            store.close()

    print(to_console(report))
    if args.output:
        write_report(report, Path(args.output))
    else:
        print()
        print(to_json(report))
    return 0 if report.cases_passed == report.cases_run and not report.failures else 1


if __name__ == "__main__":
    sys.exit(main())
