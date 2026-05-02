"""Run the strict memory content-quality gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.maintenance.quality_gate import run_quality_gate


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=None)
    updates: dict[str, Any] = {}
    if args.workspace:
        updates["workspace_id"] = args.workspace
    if args.db_path:
        updates["db_path"] = Path(args.db_path)
    return settings.model_copy(update=updates)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run memory content-quality gate.")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = _settings(args)

    conn = open_connection(settings.db_path)
    try:
        apply_migrations(conn)
        report = run_quality_gate(conn, workspace_id=settings.workspace_id)
    finally:
        close_connection(conn)

    payload = report.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"status={payload['status']}")
        print(f"workspace_id={payload['workspace_id']}")
        print(f"findings={payload['counts'].get('total_findings', 0)}")
        for finding in payload["findings"][:20]:
            print(
                f"- [{finding['severity']}] {finding['kind']} "
                f"{finding['target_type']}:{finding['target_id']} - {finding['summary']}"
            )
    return 0 if report.status == "ok" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"memory_quality_gate_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
