"""Sleep-time consolidation runner — invoked by OS scheduler every 6h.

Reads the workspace registry, opens each workspace's SQLite DB, and runs
``consolidate_workspace`` against the last 24h of episodes. Writes one
log line per workspace to stdout for the task scheduler to capture.

Idempotent: running twice in the same hour just produces a second pass;
clusters that already became insight candidates last run are still
present and get re-emitted as new candidates (the insights table does
not dedupe — the operator review queue does).

Usage::

    python scripts/memory_consolidation_runner.py [--workspace ID]
        [--window-hours 24] [--max-insights 10] [--log-file PATH]

Without ``--workspace`` the runner sweeps every registered workspace.
The scheduled task invokes this via ``pythonw.exe`` (no-console
variant) with ``--log-file`` so the console window never flashes.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

from agent_memory_lite.cognition.consolidation import consolidate_workspace

logger = logging.getLogger("agent_memory_lite.scripts.consolidation_runner")


def _redirect_streams_to_log(log_file_path: str) -> None:
    """Reopen stdout/stderr to ``log_file_path`` in append mode.

    Required when this runner is invoked via ``pythonw.exe`` from a
    scheduled task — pythonw has no console, so every stdout/stderr
    write would otherwise be silently dropped or crash. Switching the
    scheduled task from ``powershell.exe`` to ``pythonw.exe`` is what
    stops the briefly-visible console window on each 6h tick.
    """
    p = Path(log_file_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    fh = p.open("a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = fh
    sys.stderr = fh


def _load_registry(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    entries = payload.get("workspaces")
    return entries if isinstance(entries, list) else []


def _registry_path() -> Path:
    import os  # noqa: PLC0415 — local convenience import

    raw = os.environ.get("MEMORY_WORKSPACES_FILE")
    if raw:
        return Path(raw)
    return Path.home() / ".agent_memory" / "workspaces.json"


def _run_one(
    *, workspace_id: str, db_path: str, window_hours: int, max_insights: int
) -> dict[str, object]:
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as exc:
        return {"workspace_id": workspace_id, "status": "error", "error": str(exc)}
    try:
        report = consolidate_workspace(
            conn,
            workspace_id=workspace_id,
            window_hours=window_hours,
            max_insights=max_insights,
        )
        return {
            "workspace_id": report.workspace_id,
            "status": "ok",
            "episodes_seen": report.episodes_seen,
            "clusters_found": report.clusters_found,
            "insights_written": report.insights_written,
        }
    except Exception as exc:
        return {"workspace_id": workspace_id, "status": "error", "error": str(exc)}
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the sleep-time consolidation pass.")
    parser.add_argument("--workspace", help="Only consolidate this workspace (omit = sweep all).")
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--max-insights", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="Emit one JSON line per workspace.")
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help=(
            "If set, redirect stdout/stderr to this path (append, UTF-8). "
            "Required when the scheduled task invokes pythonw.exe so the "
            "console flash never appears."
        ),
    )
    args = parser.parse_args(argv)
    if args.log_file:
        _redirect_streams_to_log(args.log_file)

    registry = _load_registry(_registry_path())
    if not registry:
        sys.stderr.write("no workspaces registered — nothing to consolidate\n")
        return 0

    results: list[dict[str, object]] = []
    for entry in registry:
        if not isinstance(entry, dict):
            continue
        ws = str(entry.get("id", ""))
        db = str(entry.get("db_path", ""))
        if not ws or not db:
            continue
        if args.workspace and ws != args.workspace:
            continue
        result = _run_one(
            workspace_id=ws,
            db_path=db,
            window_hours=args.window_hours,
            max_insights=args.max_insights,
        )
        results.append(result)
        if args.json:
            sys.stdout.write(json.dumps(result) + "\n")
        elif result["status"] == "ok":
            sys.stdout.write(
                f"[{result['workspace_id']}] "
                f"episodes={result['episodes_seen']} "
                f"clusters={result['clusters_found']} "
                f"insights={result['insights_written']}\n"
            )
        else:
            sys.stdout.write(f"[{result['workspace_id']}] ERROR: {result.get('error')}\n")

    if args.workspace and not any(r["workspace_id"] == args.workspace for r in results):
        sys.stderr.write(f"workspace {args.workspace!r} not in registry\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
