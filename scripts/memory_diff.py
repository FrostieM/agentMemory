"""Compare two memory audit/watchdog/dashboard JSON reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_memory_lite.maintenance.memory_diff import diff_memory_payloads, load_memory_payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diff two memory trust JSON reports.")
    parser.add_argument("--before", required=True, help="Older audit/watchdog/dashboard JSON file.")
    parser.add_argument("--after", required=True, help="Newer audit/watchdog/dashboard JSON file.")
    parser.add_argument("--json", action="store_true")
    return parser


def _print(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(
        f"status={payload['status']} "
        f"before={payload['before_status']} after={payload['after_status']}"
    )
    if payload["count_deltas"]:
        print("count_deltas=" + json.dumps(payload["count_deltas"], ensure_ascii=False))
    if payload["component_changes"]:
        print("component_changes=" + json.dumps(payload["component_changes"], ensure_ascii=False))
    if payload["failures_added"]:
        print("failures_added=" + json.dumps(payload["failures_added"], ensure_ascii=False))
    if payload["warnings_added"]:
        print("warnings_added=" + json.dumps(payload["warnings_added"], ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = diff_memory_payloads(
            load_memory_payload(Path(args.before)),
            load_memory_payload(Path(args.after)),
        )
    except Exception as exc:
        print(f"memory_diff_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    payload = report.to_dict()
    _print(payload, as_json=args.json)
    return 2 if report.status == "degraded" else 0


if __name__ == "__main__":
    raise SystemExit(main())
