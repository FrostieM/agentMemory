"""Export durable workspace knowledge to JSON for git-tracked sync.

Phase 2.6 of v2.2 consolidation. memory.db is binary and git-unfriendly
(WAL writes flip random bytes; merging two clones is undefined). The
sync strategy is: export to JSON, commit JSON, pull JSON on the other
machine, import back into memory.db.

This script handles the export side. ``memory_import_from_json.py``
handles import. Pair them for a poor-man's multi-machine memory sync
that stays inside the local-only philosophy — no servers, no cloud
storage, just `git push` / `git pull`.

Tables exported (durable operator-curated knowledge):

* decisions, theories, theory_evidence
* concepts, insights
* behaviors, skills
* capability_links

Tables NOT exported (transient or re-derivable):

* chunks, episodes, audit_log, maintenance_events
* candidates
* chunk_symbol_metadata, symbol_edges, symbol_versions, soft_edges,
  file_digests and other derived coordination rows — all derivable from re-running
  memory_ingest_file on the source files.

Output layout::

    <out_dir>/<workspace_id>/decisions.json
    <out_dir>/<workspace_id>/theories.json
    ...
    <out_dir>/<workspace_id>/_meta.json   # schema version + timestamp

Each per-table file is::

    {"workspace_id": "...", "table": "decisions",
     "exported_at": "2026-...", "rows": [{...}, {...}]}

Usage::

    python scripts/memory_export_to_json.py \\
        --workspace agentLight \\
        --out .agent_memory/sync
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from agent_memory_lite.config.workspace_registry import WorkspaceRegistry

_DEFAULT_REGISTRY = Path.home() / ".agent_memory" / "workspaces.json"

# Tables exported in stable order. The list is deliberately small —
# every name here must be operator-curated durable knowledge, not
# transient state that re-runs would regenerate.
EXPORT_TABLES: tuple[str, ...] = (
    "decisions",
    "theories",
    "theory_evidence",
    "concepts",
    "insights",
    "behaviors",
    "skills",
    "capability_links",
)

SCHEMA_VERSION = 1


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _export_table(
    conn: sqlite3.Connection, table: str, workspace_id: str
) -> list[dict[str, object]]:
    cursor = conn.execute(
        f"SELECT * FROM {table} WHERE workspace_id = ? ORDER BY id",
        (workspace_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workspace", required=True, help="workspace_id to export")
    parser.add_argument(
        "--db-path",
        type=Path,
        help="explicit memory.db path (default: registry lookup by --workspace)",
    )
    parser.add_argument(
        "--registry",
        help="override path to workspaces.json (default: ~/.agent_memory/workspaces.json)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="output directory; written as <out>/<workspace_id>/<table>.json",
    )
    args = parser.parse_args(argv)

    db_path = _resolve_db(args)
    if not db_path.exists():
        print(f"db not found: {db_path}", file=sys.stderr)
        return 2

    out_root = args.out / args.workspace
    out_root.mkdir(parents=True, exist_ok=True)

    conn = _connect(db_path)
    try:
        exported_at = datetime.now(UTC).isoformat()
        per_table_counts: dict[str, int] = {}
        for table in EXPORT_TABLES:
            rows = _export_table(conn, table, args.workspace)
            payload = {
                "schema_version": SCHEMA_VERSION,
                "workspace_id": args.workspace,
                "table": table,
                "exported_at": exported_at,
                "rows": rows,
            }
            (out_root / f"{table}.json").write_text(
                json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
                encoding="utf-8",
            )
            per_table_counts[table] = len(rows)
        meta = {
            "schema_version": SCHEMA_VERSION,
            "workspace_id": args.workspace,
            "db_path": str(db_path),
            "exported_at": exported_at,
            "tables": per_table_counts,
        }
        (out_root / "_meta.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
    finally:
        conn.close()

    total = sum(per_table_counts.values())
    print(f"exported workspace={args.workspace!r}: {total} rows across {len(EXPORT_TABLES)} tables")
    for t, n in per_table_counts.items():
        print(f"  {t:30}  {n:>4}")
    print(f"out: {out_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
