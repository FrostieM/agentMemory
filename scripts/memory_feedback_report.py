"""Report helpful/noisy retrieval feedback."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.maintenance.feedback_report import run_usage_feedback_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize memory usage feedback.")
    parser.add_argument("--workspace", "--workspace-id", dest="workspace", default=None)
    parser.add_argument("--db-path", "--db", dest="db_path", default=None)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--min-count", type=int, default=2)
    parser.add_argument("--json", action="store_true")
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
    for item in payload["noisy_sources"]:
        print(
            "noisy="
            + json.dumps(
                {
                    "source_type": item["source_type"],
                    "source_id": item["source_id"],
                    "average_usefulness": item["average_usefulness"],
                    "feedback_count": item["feedback_count"],
                },
                ensure_ascii=False,
            )
        )
    for item in payload["helpful_sources"]:
        print(
            "helpful="
            + json.dumps(
                {
                    "source_type": item["source_type"],
                    "source_id": item["source_id"],
                    "average_usefulness": item["average_usefulness"],
                    "feedback_count": item["feedback_count"],
                },
                ensure_ascii=False,
            )
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = _settings(args)
    conn = open_connection(settings.db_path)
    try:
        report = run_usage_feedback_report(
            conn,
            workspace_id=settings.workspace_id,
            limit=args.limit,
            min_count=args.min_count,
        )
        payload = report.to_dict()
        _print(payload, as_json=args.json)
    except sqlite3.Error as exc:
        payload = {
            "status": "degraded",
            "workspace_id": settings.workspace_id,
            "failures": [f"{type(exc).__name__}: {exc}"],
        }
        _print(payload, as_json=args.json)
        return 1
    finally:
        close_connection(conn)
    return 2 if payload["status"] == "warning" else 0


if __name__ == "__main__":
    raise SystemExit(main())
