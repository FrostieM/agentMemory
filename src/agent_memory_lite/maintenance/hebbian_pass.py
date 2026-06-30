"""Phase 2: Hebbian distillation — turn co-retrieval log into soft_edges.

Reads ``retrieval_coactivation`` grouped by (workspace, query_hash). For
each group of N items, generates all pairs (a, b) with a != b. Each pair
gets a weight contribution of ``1 / (rank_a * rank_b)``: top-1+top-2 add
0.5, top-1+top-3 add 0.33, top-2+top-3 add 0.16. Pair weights compound
across queries via ``upsert_soft_edge``.

**HeLa-Mem validation gate**: a pair is only strengthened when at least
one of the two items has a positive ``outcome_score``. This prevents
reinforcing pairs both rooted in failure (the biology equivalent of
two false memories cross-cuing each other). The gate flips off with
``MEMORY_HEBBIAN_OUTCOME_GATE=false`` for legacy / debug paths.

After distillation the function prunes co-retrieval rows older than
``MEMORY_HEBBIAN_LOG_RETENTION_DAYS`` so the staging table stays bounded.

The pair-naming convention is ``"<kind>:<id>"`` (e.g.
``"decision:dec_kelly"``). That string lives in
``soft_edges.src_qualified_name`` / ``dst_qualified_name`` alongside the
code-symbol qualified names. Phase 7's ``spreading_activation`` reads
either kind without branching.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.pragmas import apply_pragmas
from agent_memory_lite.maintenance.hebbian_pass_distill import distill_workspace, qualified
from agent_memory_lite.retrieval.coactivation_log import prune_log
from agent_memory_lite.utils.time import iso_now

# ``qualified`` is re-exported for the public import surface; ``distill_workspace``
# is used internally by ``run_hebbian_pass`` below.
__all__ = [
    "HebbianReport",
    "distill_workspace",
    "qualified",
    "run_hebbian_pass",
]


@dataclass(frozen=True, slots=True)
class HebbianReport:
    workspaces_scanned: int
    edges_upserted: int
    edges_skipped_by_gate: int
    rows_pruned: int
    failed_workspaces: list[str]
    started_at: str
    finished_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "workspaces_scanned": self.workspaces_scanned,
            "edges_upserted": self.edges_upserted,
            "edges_skipped_by_gate": self.edges_skipped_by_gate,
            "rows_pruned": self.rows_pruned,
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


def run_hebbian_pass(settings: Settings) -> HebbianReport:
    """Distill the coactivation log into soft_edges across every workspace.

    Idempotent in the sense that draining the staging table on each pass
    means a second back-to-back call finds nothing to distill. Edge
    weights only ever ratchet up; the prune step keeps the staging table
    bounded.
    """
    started = iso_now()
    if not settings.hebbian_enabled:
        return HebbianReport(0, 0, 0, 0, [], started, iso_now())
    registry = _load_registry(_registry_path(settings))
    scanned = 0
    total_up = 0
    total_gated = 0
    total_pruned = 0
    failed: list[str] = []
    for entry in registry:
        if not isinstance(entry, dict):
            continue
        db_path = str(entry.get("db_path") or "")
        ws = str(entry.get("id") or "")
        if not db_path or not ws:
            continue
        scanned += 1
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            apply_pragmas(conn)
        except sqlite3.Error:
            failed.append(ws)
            # M9 (global-audit 2026-06-30): if connect succeeded but a later
            # setup call raised, the open connection would leak on this continue.
            if conn is not None:
                conn.close()
            continue
        try:
            up, gated = distill_workspace(
                conn, workspace_id=ws, outcome_gate=settings.hebbian_outcome_gate
            )
            total_up += up
            total_gated += gated
            pruned = prune_log(
                conn,
                workspace_id=ws,
                retention_days=settings.hebbian_log_retention_days,
                now_iso=started,
            )
            total_pruned += pruned
        except sqlite3.Error:
            failed.append(ws)
        finally:
            conn.close()
    return HebbianReport(
        workspaces_scanned=scanned,
        edges_upserted=total_up,
        edges_skipped_by_gate=total_gated,
        rows_pruned=total_pruned,
        failed_workspaces=failed,
        started_at=started,
        finished_at=iso_now(),
    )
