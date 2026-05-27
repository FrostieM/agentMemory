"""Forward-only SQLite migration runner."""

from __future__ import annotations

import argparse
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_memory_lite.db.migration_validation import (
    CANONICAL_BASELINE_VERSION,
    reject_legacy_pre_v3_db,
    validate_canonical_baseline,
)
from agent_memory_lite.utils.time import iso_now

MIGRATION_DIR = Path(__file__).resolve().parents[3] / "migrations"
_FILENAME_RE = re.compile(r"^\d{4}_[A-Za-z0-9_]+$")


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    path: Path


def _ensure_tracking_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )


def _applied_versions(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {str(row[0]) for row in rows}


def discover_migrations(directory: Path | None = None) -> list[Migration]:
    base = directory or MIGRATION_DIR
    if not base.exists():
        return []
    migrations: list[Migration] = []
    for path in sorted(base.glob("*.sql")):
        version = path.stem
        if not _FILENAME_RE.match(version):
            raise ValueError(f"Migration filename does not match NNNN_<slug>.sql: {path.name}")
        migrations.append(Migration(version=version, path=path))
    return migrations


def _uses_project_migration_dir(directory: Path | None) -> bool:
    return directory is None or directory.resolve() == MIGRATION_DIR.resolve()


def apply_migrations(
    conn: sqlite3.Connection,
    directory: Path | None = None,
) -> list[str]:
    """Apply any pending migrations. Returns the list of versions applied this call."""
    _ensure_tracking_table(conn)
    applied = _applied_versions(conn)
    migrations = discover_migrations(directory)
    if _uses_project_migration_dir(directory):
        if CANONICAL_BASELINE_VERSION in applied:
            validate_canonical_baseline(conn)
        else:
            reject_legacy_pre_v3_db(conn)
    pending = [m for m in migrations if m.version not in applied]
    new_versions: list[str] = []
    for migration in pending:
        conn.executescript(migration.path.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (migration.version, iso_now()),
        )
        new_versions.append(migration.version)
    if new_versions:
        conn.commit()
    return new_versions


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply agent-memory-lite migrations.")
    parser.add_argument("--db", required=True, help="SQLite database path")
    parser.add_argument("--migrations", type=Path, default=MIGRATION_DIR)
    args = parser.parse_args(argv)

    (db_path := Path(args.db)).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        applied = apply_migrations(conn, args.migrations)
    finally:
        conn.close()
    if applied:
        print("\n".join(applied))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
