"""Read-only audit-log usage report — what's actually being used.

Phase 2.1 of v2.2 consolidation: feature retirement decisions need
evidence. This script reads ``audit_log`` per workspace (or across a
hub-mode registry) and reports per-action call counts over a window.
The output is the input to deprecation conversations: actions called
<5 times in 60 days are candidates for retirement; actions called only
by automated scripts (never by an agent) are candidates for hiding from
the MCP surface.

The report is purely informational — never mutates anything, never
emits maintenance events, never writes telemetry. Run from the repo
root:

    python scripts/memory_feature_usage.py --workspace <id> --json
    python scripts/memory_feature_usage.py --all-workspaces --days 60
    python scripts/memory_feature_usage.py --workspace <id> --include-action ingest_

Output formats:
  default → human-readable two-column table sorted by call count desc.
  --json  → machine-readable list-of-dicts plus aggregates.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_memory_lite.config.workspace_registry import WorkspaceRegistry

_DEFAULT_REGISTRY = Path.home() / ".agent_memory" / "workspaces.json"


def _registry_for(args: argparse.Namespace) -> WorkspaceRegistry:
    """Resolve a WorkspaceRegistry from --registry or the standard location."""
    return WorkspaceRegistry(Path(args.registry) if args.registry else _DEFAULT_REGISTRY)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _action_counts(
    conn: sqlite3.Connection, workspace_id: str, since_iso: str
) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT action, COUNT(*) AS n
        FROM audit_log
        WHERE workspace_id = ? AND created_at >= ?
        GROUP BY action
        ORDER BY n DESC
        """,
        (workspace_id, since_iso),
    ).fetchall()
    return {row["action"]: int(row["n"]) for row in rows}


def _format_table(counts: dict[str, int], label: str) -> str:
    if not counts:
        return f"{label}: no audit_log entries in window\n"
    width = max(len(action) for action in counts)
    lines = [f"{label}:"]
    for action, n in counts.items():
        marker = ""
        if n < 5:
            marker = "  [retire candidate]"
        elif n < 20:
            marker = "  [light usage]"
        lines.append(f"  {action:<{width}}  {n:>5}{marker}")
    return "\n".join(lines) + "\n"


def _all_workspaces(args: argparse.Namespace) -> list[tuple[str, Path]]:
    """Return [(workspace_id, db_path), ...] from the registry."""
    registry = _registry_for(args)
    return [
        (entry.id, Path(entry.db_path))
        for entry in registry.list()
        if entry.db_path
    ]


def _resolve_targets(args: argparse.Namespace) -> list[tuple[str, Path]]:
    """Resolve --workspace + optional --db-path / --all-workspaces to scan list."""
    if args.all_workspaces:
        return _all_workspaces(args)
    if args.db_path:
        return [(args.workspace, args.db_path)]
    registry = _registry_for(args)
    entry = registry.get(args.workspace)
    if entry is None:
        print(f"workspace {args.workspace!r} not found in registry", file=sys.stderr)
        return []
    return [(args.workspace, Path(entry.db_path))]


def _scan_targets(
    targets: list[tuple[str, Path]],
    since_iso: str,
    include_action: str | None,
) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    """Walk every target db, return (per_ws counts, rollup counts)."""
    per_ws: dict[str, dict[str, int]] = {}
    rollup: dict[str, int] = defaultdict(int)
    for ws_id, db_path in targets:
        if not db_path.exists():
            print(f"skip: {ws_id} db not found at {db_path}", file=sys.stderr)
            continue
        conn = _connect(db_path)
        try:
            counts = _action_counts(conn, ws_id, since_iso)
        finally:
            conn.close()
        if include_action:
            counts = {a: n for a, n in counts.items() if include_action in a}
        per_ws[ws_id] = counts
        for action, n in counts.items():
            rollup[action] += n
    return per_ws, dict(rollup)


def _emit_table(
    per_ws: dict[str, dict[str, int]],
    rollup: dict[str, int],
    since_iso: str,
    days: int,
    multi_workspace: bool,
) -> None:
    print(f"window: last {days} days (since {since_iso})")
    print(f"workspaces scanned: {len(per_ws)}\n")
    for ws_id, counts in per_ws.items():
        print(_format_table(counts, f"workspace {ws_id!r}"))
    if multi_workspace and per_ws:
        rollup_sorted = dict(sorted(rollup.items(), key=lambda kv: -kv[1]))
        print(_format_table(rollup_sorted, "roll-up across all workspaces"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--workspace",
        help="single workspace_id; mutually exclusive with --all-workspaces",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        help="explicit memory.db path (default: registry lookup by --workspace)",
    )
    parser.add_argument(
        "--all-workspaces",
        action="store_true",
        help="report across every registered workspace (per-workspace + roll-up)",
    )
    parser.add_argument(
        "--registry",
        help="override path to workspaces.json (default: ~/.agent_memory/workspaces.json)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=60,
        help="window in days; actions older than --days are excluded (default 60)",
    )
    parser.add_argument(
        "--include-action",
        help="only include actions whose name contains this substring",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of a table",
    )
    args = parser.parse_args(argv)

    if not args.workspace and not args.all_workspaces:
        parser.error("--workspace or --all-workspaces is required")
    if args.workspace and args.all_workspaces:
        parser.error("--workspace and --all-workspaces are mutually exclusive")

    since_iso = (datetime.now(UTC) - timedelta(days=args.days)).isoformat()
    targets = _resolve_targets(args)
    if not targets:
        return 2
    per_ws, rollup = _scan_targets(targets, since_iso, args.include_action)

    if args.json:
        payload = {
            "since": since_iso,
            "days": args.days,
            "include_action": args.include_action,
            "workspaces": per_ws,
            "rollup": dict(sorted(rollup.items(), key=lambda kv: -kv[1])),
        }
        print(json.dumps(payload, indent=2))
        return 0

    _emit_table(per_ws, rollup, since_iso, args.days, multi_workspace=args.all_workspaces)
    return 0


if __name__ == "__main__":
    sys.exit(main())
