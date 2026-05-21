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
from agent_memory_lite.repositories.soft_edges_repo import upsert_soft_edge
from agent_memory_lite.retrieval.coactivation_log import prune_log
from agent_memory_lite.utils.time import iso_now

# Kinds whose outcome_score column lives in their own table (Phase 1).
# The HeLa-Mem gate looks up these scores; unknown kinds default to 0.
_KIND_TABLE: dict[str, str] = {
    "decision": "decisions",
    "theory": "theories",
    "behavior": "behaviors",
    "skill": "skills",
    "insight": "insights",
    "chunk": "chunks",
}


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


def qualified(kind: str, item_id: str) -> str:
    """Synthetic qualified name for a memory row in the soft_edges table."""
    return f"{kind}:{item_id}"


def _outcome_score(conn: sqlite3.Connection, kind: str, item_id: str) -> float:
    """Look up outcome_score for one row. Returns 0.0 when missing."""
    table = _KIND_TABLE.get(kind)
    if not table:
        return 0.0
    try:
        row = conn.execute(f"SELECT outcome_score FROM {table} WHERE id = ?", (item_id,)).fetchone()
    except sqlite3.OperationalError:
        return 0.0
    if row is None:
        return 0.0
    return float(row[0] or 0.0)


def distill_workspace(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    outcome_gate: bool = True,
    min_group_size: int = 2,
) -> tuple[int, int]:
    """Distill one workspace's coactivation log into soft_edges.

    Returns (edges_upserted, edges_skipped_by_gate).
    """
    try:
        rows = conn.execute(
            "SELECT query_hash, item_kind, item_id, rank FROM retrieval_coactivation "
            "WHERE workspace_id = ? ORDER BY query_hash, rank",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return 0, 0
    if not rows:
        return 0, 0
    # Group by query_hash.
    groups: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        groups.setdefault(row["query_hash"], []).append(row)
    upserted = 0
    gated = 0
    # Round-2 audit (M3): the i<j pair loop below is O(N^2) in the group
    # size. A single query that logged 500 co-activations would
    # enumerate ~125k upsert_soft_edge calls in one synchronous pass on
    # a background thread (long lock hold / DoS). ``items`` is already
    # rank-ordered by the SELECT, so cap to the top-N most-relevant
    # co-activations per group — bounds the pair count at N*(N-1)/2.
    max_group_items = 20
    for items in groups.values():
        if len(items) < min_group_size:
            continue
        capped = items[:max_group_items]
        for i in range(len(capped)):
            for j in range(i + 1, len(capped)):
                a, b = capped[i], capped[j]
                if a["item_kind"] == b["item_kind"] and a["item_id"] == b["item_id"]:
                    continue
                if outcome_gate:
                    score_a = _outcome_score(conn, a["item_kind"], a["item_id"])
                    score_b = _outcome_score(conn, b["item_kind"], b["item_id"])
                    # HeLa-Mem gate: skip ONLY when BOTH sides are strictly
                    # negative. Earlier version used ``<= 0`` which also
                    # banned neutral [0, 0] pairs and effectively froze the
                    # graph on fresh workspaces (every decision starts at
                    # outcome_score=0 until feedback propagates). Empirical
                    # probe on copyBot (250 decisions, all outcome=0)
                    # surfaced this regression: 4 pairs, all gated, zero
                    # edges. The new gate only blocks pairs rooted in
                    # demonstrated failure -- neutral pairs accumulate
                    # normally so the Hebbian graph can bootstrap.
                    if score_a < 0.0 and score_b < 0.0:
                        gated += 1
                        continue
                rank_a = max(1, int(a["rank"] or 1))
                rank_b = max(1, int(b["rank"] or 1))
                increment = 1.0 / float(rank_a * rank_b)
                src = qualified(a["item_kind"], a["item_id"])
                dst = qualified(b["item_kind"], b["item_id"])
                # Order-stable: store the smaller qualified name as src so
                # we don't double-count the same undirected pair.
                if src > dst:
                    src, dst = dst, src
                try:
                    upsert_soft_edge(
                        conn,
                        workspace_id=workspace_id,
                        src=src,
                        dst=dst,
                        kind="co_retrieved",
                        weight_increment=increment,
                    )
                    upserted += 1
                except (sqlite3.Error, ValueError):
                    continue
    conn.commit()
    return upserted, gated


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
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error:
            failed.append(ws)
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
