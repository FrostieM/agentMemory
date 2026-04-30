"""Summarize memory watchdog/audit artifact history."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.maintenance.trends import build_trend_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report recent memory trust trend history.")
    parser.add_argument("--audit-dir", default=None)
    parser.add_argument("--db-path", "--db", dest="db_path", default=None)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    return parser


def _audit_dir(args: argparse.Namespace) -> Path:
    if args.audit_dir:
        return Path(args.audit_dir)
    settings = Settings(_env_file=None)
    db_path = Path(args.db_path) if args.db_path else settings.db_path
    return db_path.parent / "audit_runs"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = build_trend_report(_audit_dir(args), limit=args.limit)
    payload = report.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"status={payload['status']} audit_dir={payload['audit_dir']}")
        print("counts=" + json.dumps(payload["counts"], ensure_ascii=False, sort_keys=True))
        for run in payload["runs"]:
            print(
                f"{run['generated_at']} status={run['status']} "
                f"retrieval={run['retrieval_eval_status']} hygiene={run['hygiene_status']}"
            )
    return 0 if report.status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
