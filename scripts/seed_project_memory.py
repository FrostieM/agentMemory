"""Seed neutral memory-population helpers into a local memory DB.

The seed is intentionally limited to capability/concept objects that help agents
populate memory correctly. It does not write behavior instructions, language
preferences, communication style, personality, or project-specific roles.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_memory_lite.bootstrap.project_memory_seed import seed_neutral_project_memory
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import apply_migrations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed neutral memory-population helpers into a memory DB.",
    )
    parser.add_argument("--workspace", "--workspace-id", required=True, dest="workspace_id")
    parser.add_argument("--db-path", "--db", required=True, type=Path, dest="db_path")
    parser.add_argument(
        "--no-migrate",
        action="store_true",
        help="Do not apply pending schema migrations before seeding.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    conn = open_connection(args.db_path)
    try:
        applied = [] if args.no_migrate else apply_migrations(conn)
        result = seed_neutral_project_memory(conn, workspace_id=args.workspace_id)
    finally:
        close_connection(conn)

    payload = {
        "status": "ok",
        "db_path": str(args.db_path),
        "migrations_applied": applied,
        **result.to_dict(),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"Seeded neutral memory bootstrap for workspace={args.workspace_id!r} db={args.db_path}"
        )
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
