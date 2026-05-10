"""Import workspace knowledge from a memory_export_to_json.py dump.

Phase 2.6 of v2.2 consolidation. Pair with ``memory_export_to_json.py``:
the export side serializes durable operator-curated tables to JSON;
this script rehydrates them into a live memory.db. Safe for "git pull
on a different machine" workflow.

Behavior:

* ``--dry-run`` (default): report what would change, write nothing.
* ``--apply``: actually upsert. Each row is written via ``INSERT OR
  REPLACE`` keyed on ``id``. Rows missing in the import file but present
  in the destination DB are LEFT untouched (this is a sync-forward
  tool, not a destructive mirror; an explicit
  ``--prune`` flag would delete them but is intentionally NOT
  implemented in v1 — too easy to wipe a hot workspace).

The script refuses to import into a workspace different from the file's
``workspace_id`` unless ``--allow-rename`` is set, which rewrites the
workspace_id of every imported row to match ``--workspace``.

Usage::

    # dry-run from a git-tracked sync dir
    python scripts/memory_import_from_json.py \\
        --workspace agentLight \\
        --in .agent_memory/sync

    # apply
    python scripts/memory_import_from_json.py \\
        --workspace agentLight \\
        --in .agent_memory/sync \\
        --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from agent_memory_lite.config.workspace_registry import WorkspaceRegistry

_DEFAULT_REGISTRY = Path.home() / ".agent_memory" / "workspaces.json"

# Must match the export side. Re-imported in stable order so foreign-
# key-style dependencies resolve (theories before theory_evidence,
# decisions before capability_links, etc. — no FK constraints in the
# schema, but order makes diff diagnostics readable).
IMPORT_TABLES: tuple[str, ...] = (
    "decisions",
    "theories",
    "theory_evidence",
    "domain_concepts",
    "research_insights",
    "behavior_instructions",
    "agent_roles",
    "agent_skills",
    "agent_playbooks",
    "capability_links",
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _existing_ids(conn: sqlite3.Connection, table: str, workspace_id: str) -> set[str]:
    rows = conn.execute(
        f"SELECT id FROM {table} WHERE workspace_id = ?", (workspace_id,)
    ).fetchall()
    return {r["id"] for r in rows}


def _resolve_db(args: argparse.Namespace) -> Path:
    if args.db_path:
        return args.db_path
    registry_path = Path(args.registry) if args.registry else _DEFAULT_REGISTRY
    registry = WorkspaceRegistry(registry_path)
    entry = registry.get(args.workspace)
    if entry is None:
        print(f"workspace {args.workspace!r} not found in registry", file=sys.stderr)
        sys.exit(2)
    return Path(entry.db_path)


def _load_table_payload(
    in_root: Path, table: str, target_workspace: str, allow_rename: bool
) -> list[dict[str, object]]:
    file_path = in_root / f"{table}.json"
    if not file_path.exists():
        return []
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    src_ws = payload.get("workspace_id")
    if src_ws != target_workspace and not allow_rename:
        print(
            f"refuse: {file_path} carries workspace_id={src_ws!r} but importing "
            f"into {target_workspace!r}; pass --allow-rename to override",
            file=sys.stderr,
        )
        sys.exit(3)
    rows = list(payload.get("rows") or [])
    if allow_rename:
        for row in rows:
            row["workspace_id"] = target_workspace
    return rows


def _upsert_rows(
    conn: sqlite3.Connection, table: str, rows: list[dict[str, object]], dry_run: bool
) -> tuple[int, int]:
    if not rows:
        return 0, 0
    cols = _columns(conn, table)
    common = [c for c in cols if c in rows[0]]
    placeholders = ", ".join("?" for _ in common)
    sql = f"INSERT OR REPLACE INTO {table} ({', '.join(common)}) VALUES ({placeholders})"
    inserted = 0
    skipped = 0
    for row in rows:
        if not all(c in row for c in common):
            skipped += 1
            continue
        if not dry_run:
            conn.execute(sql, [row[c] for c in common])
        inserted += 1
    return inserted, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workspace", required=True, help="destination workspace_id")
    parser.add_argument(
        "--in",
        dest="in_dir",
        type=Path,
        required=True,
        help="root directory written by memory_export_to_json.py",
    )
    parser.add_argument("--db-path", type=Path, help="explicit memory.db path")
    parser.add_argument("--registry", help="override path to workspaces.json")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually write; default is dry-run reporting deltas",
    )
    parser.add_argument(
        "--allow-rename",
        action="store_true",
        help="rewrite workspace_id on every imported row to match --workspace",
    )
    parser.add_argument(
        "--source-workspace",
        help=(
            "name of the subdir under --in to read from (defaults to --workspace). "
            "Use this when migrating between workspace names with --allow-rename."
        ),
    )
    args = parser.parse_args(argv)

    source_ws = args.source_workspace or args.workspace
    in_root = args.in_dir / source_ws
    if not in_root.exists():
        print(f"import dir not found: {in_root}", file=sys.stderr)
        return 2
    db_path = _resolve_db(args)
    if not db_path.exists():
        print(f"db not found: {db_path}", file=sys.stderr)
        return 2

    conn = _connect(db_path)
    if args.apply:
        conn.execute("BEGIN")
    try:
        per_table: list[tuple[str, int, int, int]] = []
        for table in IMPORT_TABLES:
            rows = _load_table_payload(in_root, table, args.workspace, args.allow_rename)
            existing_before = _existing_ids(conn, table, args.workspace)
            new_ids = {row["id"] for row in rows if "id" in row}
            net_new = new_ids - existing_before
            inserted, skipped = _upsert_rows(conn, table, rows, dry_run=not args.apply)
            per_table.append((table, inserted, len(net_new), skipped))
        if args.apply:
            conn.commit()
    except Exception:
        if args.apply:
            conn.rollback()
        raise
    finally:
        conn.close()

    label = "apply" if args.apply else "dry-run"
    print(f"{label} workspace={args.workspace!r} from {in_root}")
    print(f"  {'table':<28} {'upserted':>9} {'net-new':>8} {'skipped':>8}")
    total_upserted = 0
    for table, inserted, net_new, skipped in per_table:
        print(f"  {table:<28} {inserted:>9} {net_new:>8} {skipped:>8}")
        total_upserted += inserted
    print(f"\ntotal upserted: {total_upserted}")
    if not args.apply:
        print("(no writes performed; pass --apply to commit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
