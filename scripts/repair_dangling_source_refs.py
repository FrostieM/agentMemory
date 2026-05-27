"""Null out dangling source_episode_id / supersedes_decision_id refs.

When episodes (or decisions) are bulk-deleted while FK enforcement is off,
child rows keep their `source_episode_id` pointing at rows that no longer
exist. SQLite ``PRAGMA foreign_key_check`` reports them; this script
nulls those orphaned references and reports counts per table.

Usage::

    python scripts/repair_dangling_source_refs.py \\
        --db-path .agent_memory/memory.db --apply --backup-first

Default is dry-run.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

# (child_table, child_column, parent_table) tuples we know are nullable.
# `chunks.episode_id` is the column name on the chunks table; everywhere
# else it's `source_episode_id`. Several capability tables also keep the
# audit-trail link.
NULLABLE_REFS: tuple[tuple[str, str, str], ...] = (
    ("capability_links", "source_episode_id", "episodes"),
    ("chunks", "episode_id", "episodes"),
    ("theory_evidence", "source_episode_id", "episodes"),
    ("decisions", "source_episode_id", "episodes"),
    ("behaviors", "source_episode_id", "episodes"),
    ("skills", "source_episode_id", "episodes"),
    ("candidates", "source_episode_id", "episodes"),
    ("decisions", "supersedes_decision_id", "decisions"),
)


def _backup(db_path: Path) -> Path:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    backup = db_path.with_name(db_path.name + f".bak-fkrepair-{ts}")
    shutil.copy2(db_path, backup)
    return backup


def _existing_ids(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[0] for row in conn.execute(f"SELECT id FROM {table}")}


def _null_dangling(
    conn: sqlite3.Connection,
    *,
    child_table: str,
    child_column: str,
    parent_ids: set[str],
    apply: bool,
) -> int:
    rows = conn.execute(
        f"SELECT id, {child_column} FROM {child_table} WHERE {child_column} IS NOT NULL"
    ).fetchall()
    dangling = [row[0] for row in rows if row[1] is not None and row[1] not in parent_ids]
    if not dangling:
        return 0
    if apply:
        conn.executemany(
            f"UPDATE {child_table} SET {child_column} = NULL WHERE id = ?",
            [(rid,) for rid in dangling],
        )
    return len(dangling)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-first", action="store_true")
    args = parser.parse_args(argv)

    db_path: Path = args.db_path.resolve()
    if not db_path.exists():
        print(f"db not found: {db_path}", file=sys.stderr)
        return 2

    if args.apply and args.backup_first:
        backup = _backup(db_path)
        print(f"backup: {backup}")

    conn = sqlite3.connect(db_path)
    summary: Counter[tuple[str, str, str]] = Counter()
    try:
        if args.apply:
            conn.execute("BEGIN")
        # Cache parent ids per parent table.
        parent_cache: dict[str, set[str]] = {}
        for child_table, child_column, parent_table in NULLABLE_REFS:
            try:
                parent_ids = parent_cache.get(parent_table)
                if parent_ids is None:
                    parent_ids = _existing_ids(conn, parent_table)
                    parent_cache[parent_table] = parent_ids
                count = _null_dangling(
                    conn,
                    child_table=child_table,
                    child_column=child_column,
                    parent_ids=parent_ids,
                    apply=args.apply,
                )
                summary[(child_table, child_column, parent_table)] = count
            except sqlite3.OperationalError as exc:
                # Schema doesn't have this table/column on this workspace.
                print(f"  skip {child_table}.{child_column}: {exc}")
        if args.apply:
            conn.execute("COMMIT")
    except Exception:
        if args.apply:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    mode = "APPLY" if args.apply else "dry-run"
    print(f"\nmode: {mode}")
    print(f"{'child':28} {'column':24} {'parent':14} {'dangling':>8}")
    print("-" * 80)
    total = 0
    for (child, column, parent), count in summary.most_common():
        print(f"{child:28} {column:24} {parent:14} {count:>8}")
        total += count
    print("-" * 80)
    print(f"{'TOTAL':28} {'':24} {'':14} {total:>8}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
