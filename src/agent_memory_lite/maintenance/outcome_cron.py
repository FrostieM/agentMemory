"""Sentinel-scheduled outcome_score refresh.

Reads every workspace registered in ``workspaces.json`` and runs
``outcome_recompute.refresh_workspace`` against each. Idempotent --
re-runs that find no signal change write zero rows.

This module is *failure-soft*: a single workspace DB failure does not
abort the sweep. The whole pass writes a single audit row at the end
so the operator can see when outcomes last updated globally.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from agent_memory_lite.cognition.outcome_recompute import refresh_workspace
from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.pragmas import apply_pragmas
from agent_memory_lite.utils.time import iso_now


@dataclass(frozen=True, slots=True)
class CronReport:
    workspaces_scanned: int
    rows_updated: dict[str, int]
    failed_workspaces: list[str]
    started_at: str
    finished_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "workspaces_scanned": self.workspaces_scanned,
            "rows_updated": dict(self.rows_updated),
            "failed_workspaces": list(self.failed_workspaces),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _registry_path(settings: Settings) -> Path:
    raw = os.environ.get("MEMORY_WORKSPACES_FILE") or str(settings.workspaces_file)
    return Path(raw).expanduser()


def _load_registry(path: Path) -> list[dict[str, str]]:
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


def _run_one(db_path: str, workspace_id: str, now_iso: str) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        # round-C: apply_pragmas + row_factory moved INSIDE the try -- if apply_pragmas
        # raised (e.g. read-only DB / I/O error), the connection leaked because the
        # finally was never entered (the round-B apply_pragmas swap reintroduced the
        # M9 leak shape here).
        conn.row_factory = sqlite3.Row
        apply_pragmas(conn)
        out = refresh_workspace(conn, workspace_id=workspace_id, now_iso=now_iso)
        conn.commit()
        return out
    finally:
        conn.close()


def run_outcome_sweep(settings: Settings) -> CronReport:
    """Recompute outcome_score across every registered workspace."""
    if not settings.outcome_loop_enabled:
        return CronReport(0, {}, [], iso_now(), iso_now())
    started = iso_now()
    registry = _load_registry(_registry_path(settings))
    aggregated: dict[str, int] = {}
    failed: list[str] = []
    scanned = 0
    for entry in registry:
        if not isinstance(entry, dict):
            continue
        db_path = str(entry.get("db_path") or "")
        ws = str(entry.get("id") or "")
        if not db_path or not ws:
            continue
        scanned += 1
        try:
            counts = _run_one(db_path, ws, started)
        except sqlite3.Error:
            failed.append(ws)
            continue
        for kind, n in counts.items():
            aggregated[kind] = aggregated.get(kind, 0) + n
    return CronReport(
        workspaces_scanned=scanned,
        rows_updated=aggregated,
        failed_workspaces=failed,
        started_at=started,
        finished_at=iso_now(),
    )
