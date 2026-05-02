"""Safely apply bounded memory hygiene auto-triage actions."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.maintenance.auto_triage import triage_capability_links
from agent_memory_lite.utils.time import iso_now


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply safe, explicit auto-triage for memory hygiene findings."
    )
    parser.add_argument("--workspace", "--workspace-id", dest="workspace", default=None)
    parser.add_argument("--db-path", "--db", dest="db_path", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Mutate memory. Default is dry-run only.",
    )
    parser.add_argument(
        "--backup-first",
        action="store_true",
        help="Copy memory.db before applying mutations. Required with --apply.",
    )
    parser.add_argument("--min-strength", type=float, default=0.6)
    parser.add_argument("--min-match-score", type=float, default=1.0)
    parser.add_argument("--max-links-per-target", type=int, default=1)
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


def _backup_db(db_path: Path) -> str | None:
    if not db_path.exists():
        return None
    stamp = iso_now().replace(":", "").replace("-", "").replace("+", "Z")
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"memory_before_auto_triage_{stamp}.db"
    shutil.copy2(db_path, target)
    return str(target)


def _print(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"status={payload['status']} workspace_id={payload['workspace_id']}")
    print(f"dry_run={payload['dry_run']}")
    print(
        "before_counts=" + json.dumps(payload["before_counts"], ensure_ascii=False, sort_keys=True)
    )
    print("after_counts=" + json.dumps(payload["after_counts"], ensure_ascii=False, sort_keys=True))
    if payload.get("backup"):
        print(f"backup={payload['backup']}")
    for item in payload["applied_links"]:
        action = "would_link" if payload["dry_run"] else "linked"
        print(
            f"{action} {item['target_type']}:{item['target_id']} -> "
            f"{item['capability_type']}:{item['capability_name']} "
            f"relation={item['relation']} strength={item['strength']} "
            f"match_score={item['match_score']}"
        )
    for item in payload["skipped_findings"]:
        print(
            f"skipped {item['target_type']}:{item['target_id']} - {item['reason']} "
            f"best_strength={item['best_strength']} best_match_score={item['best_match_score']}"
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.apply and not args.backup_first:
        print("Refusing auto-triage apply without --backup-first.", file=sys.stderr)
        return 1
    if args.max_links_per_target < 1:
        print("--max-links-per-target must be >= 1.", file=sys.stderr)
        return 1

    settings = _settings(args)
    backup: str | None = None
    conn = open_connection(settings.db_path)
    try:
        if args.apply:
            backup = _backup_db(settings.db_path)
        result = triage_capability_links(
            conn,
            workspace_id=settings.workspace_id,
            apply=args.apply,
            min_strength=args.min_strength,
            min_match_score=args.min_match_score,
            max_links_per_target=args.max_links_per_target,
            importance_threshold=args.importance_threshold,
        )
        payload = result.to_dict()
        if backup:
            payload["backup"] = backup
        _print(payload, as_json=args.json)
    except Exception as exc:
        print(f"memory_auto_triage_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        close_connection(conn)

    if result.status == "ok":
        return 0
    if result.status == "improved":
        return 0 if args.apply else 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
