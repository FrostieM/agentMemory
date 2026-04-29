"""Read-only review queue report for memory candidates."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.models.enums import MemoryCandidateStatus
from agent_memory_lite.repositories.candidates_repo import list_candidates


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report reviewable memory candidates.")
    parser.add_argument("--workspace", "--workspace-id", dest="workspace", default=None)
    parser.add_argument("--db-path", "--db", dest="db_path", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--stale-days", type=int, default=14)
    parser.add_argument("--status", action="append", default=["new"])
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=None)
    updates: dict[str, Any] = {}
    if args.workspace:
        updates["workspace_id"] = args.workspace
    if args.db_path:
        updates["db_path"] = Path(args.db_path)
    return settings.model_copy(update=updates)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _age_days(value: str | None) -> int | None:
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    return int((datetime.now(UTC) - parsed).total_seconds() // 86400)


def run_candidate_triage(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    statuses: list[str],
    limit: int,
    stale_days: int,
) -> dict[str, Any]:
    parsed_statuses = [MemoryCandidateStatus(status) for status in statuses]
    candidates = list_candidates(
        conn,
        workspace_id=workspace_id,
        statuses=parsed_statuses,
        limit=limit,
    )
    rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    kind_counts: Counter[str] = Counter()
    stale_count = 0
    high_value_count = 0
    for candidate in candidates:
        age = _age_days(candidate.created_at)
        stale = age is not None and age >= stale_days
        high_value = candidate.importance >= 0.8 or candidate.confidence >= 0.85
        if stale:
            stale_count += 1
        if high_value:
            high_value_count += 1
        status_counts[candidate.status.value] += 1
        kind_counts[candidate.kind.value] += 1
        rows.append(
            {
                "candidate_id": candidate.id,
                "status": candidate.status.value,
                "kind": candidate.kind.value,
                "subject": candidate.subject,
                "predicate": candidate.predicate,
                "object": candidate.object,
                "confidence": candidate.confidence,
                "importance": candidate.importance,
                "source_episode_id": candidate.source_episode_id,
                "age_days": age,
                "stale": stale,
                "high_value": high_value,
                "review_hint": (
                    "promote_or_reject_now"
                    if stale or high_value
                    else "review_when_related_context_is_active"
                ),
            }
        )
    status = "warning" if stale_count or high_value_count else "ok"
    return {
        "status": status,
        "workspace_id": workspace_id,
        "counts": {
            "returned": len(rows),
            "stale": stale_count,
            "high_value": high_value_count,
            "by_status": dict(status_counts),
            "by_kind": dict(kind_counts),
        },
        "candidates": rows,
        "warnings": [
            "stale candidates require promote/reject review" if stale_count else "",
            "high-value candidates require explicit review" if high_value_count else "",
        ],
    }


def _print(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"status={payload['status']} workspace_id={payload['workspace_id']}")
    print("counts=" + json.dumps(payload["counts"], ensure_ascii=False, sort_keys=True))
    for candidate in payload["candidates"]:
        print(
            f"{candidate['candidate_id']} {candidate['kind']} {candidate['status']} "
            f"age={candidate['age_days']} confidence={candidate['confidence']:.2f} "
            f"importance={candidate['importance']:.2f} {candidate['review_hint']}"
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = _settings(args)
    conn = open_connection(settings.db_path)
    try:
        payload = run_candidate_triage(
            conn,
            workspace_id=settings.workspace_id,
            statuses=args.status,
            limit=args.limit,
            stale_days=args.stale_days,
        )
        payload["warnings"] = [warning for warning in payload["warnings"] if warning]
        _print(payload, as_json=args.json)
    except Exception as exc:
        print(f"memory_candidate_triage_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        close_connection(conn)
    return 0 if payload["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
