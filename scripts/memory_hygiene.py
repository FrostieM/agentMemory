"""Print a detailed memory hygiene report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.maintenance.hygiene import run_hygiene_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report memory content-discipline gaps.")
    parser.add_argument("--workspace", "--workspace-id", dest="workspace", default=None)
    parser.add_argument("--db-path", "--db", dest="db_path", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--candidate-stale-days", type=int, default=14)
    parser.add_argument("--experiment-stale-days", type=int, default=30)
    parser.add_argument("--importance-threshold", type=float, default=0.8)
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=None)
    updates: dict[str, Any] = {}
    if args.workspace:
        updates["workspace_id"] = args.workspace
    if args.db_path:
        updates["db_path"] = Path(args.db_path)
    return settings.model_copy(update=updates)


def _print(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"status={payload['status']} workspace_id={payload['workspace_id']}")
    print("counts=" + json.dumps(payload["counts"], ensure_ascii=False, sort_keys=True))
    for finding in payload["findings"]:
        print(
            f"{finding['severity']} {finding['kind']} "
            f"{finding['target_type']}:{finding['target_id']} - {finding['summary']}"
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = _settings(args)
    conn = open_connection(settings.db_path)
    try:
        report = run_hygiene_report(
            conn,
            workspace_id=settings.workspace_id,
            candidate_stale_days=args.candidate_stale_days,
            experiment_stale_days=args.experiment_stale_days,
            importance_threshold=args.importance_threshold,
        )
        payload = report.to_dict()
        _print(payload, as_json=args.json)
    except Exception as exc:
        print(f"memory_hygiene_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        close_connection(conn)
    return 0 if report.status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
