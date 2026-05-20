"""v3.4 #9 — hub-mode cross-DB transfer for V2 recall tuning.

``recall_tuning.suggest_params`` already does two-tier rollup:

1. own workspace (last ``window_hours``)
2. same-DB transfer — aggregate across ALL workspaces inside the
   current SQLite (v3.3 ``MEMORY_RECALL_TRANSFER_ENABLED``).

This module adds the third tier — cross-DB transfer. When the
workspace is fresh enough that even the same-DB pool is thin (e.g.
the operator just spun up a new project with its own
``.agent_memory/memory.db`` and never had a sibling DB next to it),
we walk ``~/.agent_memory/workspaces.json`` and aggregate
``recall_history`` rows from every OTHER registered DB. The
suggestion comes back marked ``transfer_cross_db:`` so the
operator can grep audit rows by source.

Design choices that keep this safe:

* **Opt-in, default OFF.** ``MEMORY_RECALL_TRANSFER_CROSS_DB_ENABLED``
  defaults to false because pulling tuning signal from unrelated
  projects is more aggressive than same-DB pollination. The
  operator turns it on when they want a fresh project to inherit
  recall priors from a mature sibling.
* **Read-only.** We open each peer DB with the standard sqlite3
  connection and only ever SELECT — no writes ever leave the
  module. Schema mismatches return None instead of raising.
* **Skip the current DB.** The runner passes its own
  ``current_db_path`` so we never double-count rows that the
  same-DB tier already saw.
* **Higher sample bar.** Default ``MIN_SAMPLES = 32`` — even
  noisier than same-DB transfer (16) because cross-project
  topics share less vocabulary.
* **Bounded fan-out.** Caps at ``MAX_PEER_DBS`` (default 8)
  because each peer DB costs one open + one query. Most operators
  have ≤5 workspaces; the cap protects pathological registries.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, floor: int = 1) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return max(floor, int(raw))
    except ValueError:
        return default


def is_cross_db_enabled() -> bool:
    """v3.4: default OFF. Operator opts in via
    ``MEMORY_RECALL_TRANSFER_CROSS_DB_ENABLED=true`` when they want
    cold-start workspaces to inherit recall priors from siblings in
    other DBs of the same registry."""
    return _bool_env("MEMORY_RECALL_TRANSFER_CROSS_DB_ENABLED", False)


def cross_db_min_samples() -> int:
    """Min rows across peer DBs before the suggestion can fire. Higher
    than same-DB (16) because mixed-project signal is noisier."""
    return _int_env("MEMORY_RECALL_TRANSFER_CROSS_DB_MIN_SAMPLES", 32)


def max_peer_dbs() -> int:
    """Cap on peer DBs scanned per call. Bounds the per-recall fan-out
    cost in pathological registries."""
    return _int_env("MEMORY_RECALL_TRANSFER_CROSS_DB_MAX_PEERS", 8)


def _registry_path() -> Path:
    return Path(
        os.environ.get("MEMORY_WORKSPACES_FILE")
        or (Path.home() / ".agent_memory" / "workspaces.json")
    )


def _load_peer_db_paths(current_db_path: str) -> list[str]:
    """Return registered db_paths excluding the current DB. Missing
    files are dropped (a stale registry entry pointing at a deleted
    project is common — see ``setup_agent.py --doctor``)."""
    reg = _registry_path()
    if not reg.exists():
        return []
    try:
        payload = json.loads(reg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = payload.get("workspaces") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    current_norm = os.path.normcase(os.path.abspath(current_db_path))
    out: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        peer = entry.get("db_path")
        if not isinstance(peer, str) or not peer:
            continue
        peer_norm = os.path.normcase(os.path.abspath(peer))
        if peer_norm == current_norm:
            continue  # skip same DB — same-DB tier already saw these rows
        if not Path(peer).exists():
            continue  # stale registry entry
        out.append(peer)
        if len(out) >= max_peer_dbs():
            break
    return out


def _peer_rollup(peer_db_path: str, *, window_hours: int) -> tuple[int, float, float] | None:
    """Run the same aggregation query as ``recall_tuning._rollup_stats``
    against one peer DB. Returns ``(n, empty_count, outcome_sum_x100)``
    so the caller can stream-aggregate across peers without double-
    averaging. Failure-soft on missing schema / locked DB / etc."""
    try:
        conn = sqlite3.connect(peer_db_path)
        # uri=False keeps the connection lightweight; we never write so
        # transactional behaviour doesn't matter.
        row = conn.execute(
            "SELECT COUNT(*) AS n, "
            "SUM(CASE WHEN hits_count = 0 THEN 1 ELSE 0 END) AS empties, "
            "SUM(COALESCE(avg_outcome_x100, 0)) AS outcome_sum_x100 "
            "FROM recall_history "
            f"WHERE created_at >= datetime('now', '-{int(window_hours)} hours')",
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    n = int(row[0] or 0)
    if n == 0:
        return None
    empties = int(row[1] or 0)
    outcome_sum_x100 = float(row[2] or 0.0)
    return n, float(empties), outcome_sum_x100


def rollup_cross_db_stats(
    *, current_db_path: str, window_hours: int
) -> tuple[int, float, float] | None:
    """Aggregate ``recall_history`` across every other registered DB.

    Returns ``(n, empty_rate, mean_outcome)`` matching the shape of
    ``recall_tuning._rollup_stats`` so the caller can hand the tuple
    to ``_apply_rules`` unchanged. Returns ``None`` when disabled,
    when no peers are reachable, or when total samples are zero."""
    if not is_cross_db_enabled():
        return None
    peers = _load_peer_db_paths(current_db_path)
    if not peers:
        return None
    total_n = 0
    total_empties = 0.0
    total_outcome_sum_x100 = 0.0
    for peer in peers:
        result = _peer_rollup(peer, window_hours=window_hours)
        if result is None:
            continue
        n, empties, outcome_sum_x100 = result
        total_n += n
        total_empties += empties
        total_outcome_sum_x100 += outcome_sum_x100
    if total_n == 0:
        return None
    empty_rate = total_empties / float(total_n)
    mean_outcome = (total_outcome_sum_x100 / float(total_n)) / 100.0
    return total_n, empty_rate, mean_outcome
