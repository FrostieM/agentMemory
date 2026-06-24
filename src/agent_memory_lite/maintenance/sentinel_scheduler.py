"""Trigger-on-traffic sentinel runner.

Every compact read path can invoke ``maybe_run_sentinels``. If
``workspace_meta.last_sentinel_run_at`` is older than
``MEMORY_SENTINEL_AUTORUN_HOURS``, a background daemon thread runs
the workspace's ``retrieval_sentinels.yaml`` cases and persists
results. Disabled by default (hours=0).

The thread opens its own SQLite connection. Failures swallowed and
logged — sentinels are idempotent, the next overdue tick retries.
A per-workspace ``threading.Lock`` (``sentinel_lock``) blocks a
second concurrent run; without it, two simultaneous traffic-triggered
checks would both pass the overdue check and spawn duplicate daemons.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

from agent_memory_lite.config import workspace_meta
from agent_memory_lite.config.settings import Settings
from agent_memory_lite.config.workspace_paths import connection_matches_workspace
from agent_memory_lite.db.pragmas import enable_foreign_keys
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.maintenance.retrieval_quality import (
    load_retrieval_quality_cases,
    run_retrieval_quality_evals,
)
from agent_memory_lite.maintenance.sentinel_lock import get_workspace_lock
from agent_memory_lite.maintenance.sentinel_persistence import (
    SentinelResultIn,
    record_sentinel_run,
)
from agent_memory_lite.maintenance.sentinel_scheduler_brain import (
    _maybe_run_brain_pass,
    _stamp_last_run,
)
from agent_memory_lite.maintenance.sentinel_scheduler_status import (
    _is_overdue,
    _to_sentinel_status,
)
from agent_memory_lite.maintenance.sentinels import discover_sentinel_file
from agent_memory_lite.utils.time import iso_now
from agent_memory_lite.vector_store.base import VectorStore

# Re-exported for the original public import surface; ``_stamp_last_run`` is a
# standalone helper not referenced inside this module.
__all__ = [
    "_is_overdue",
    "_maybe_run_brain_pass",
    "_run_sentinel_pass",
    "_stamp_last_run",
    "_to_sentinel_status",
    "maybe_run_sentinels",
]

_LAST_RUN_KEY = "last_sentinel_run_at"
_log = logging.getLogger(__name__)


def maybe_run_sentinels(
    *,
    workspace_id: str,
    settings: Settings,
    db_path: Path,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> bool:
    """Decide if a sentinel pass is overdue. If so, fire it on a thread.

    Returns True iff a run was scheduled. Never blocks the caller.
    """
    if settings.sentinel_autorun_hours <= 0:
        return False
    if not _is_overdue(
        db_path=db_path, workspace_id=workspace_id, threshold_hours=settings.sentinel_autorun_hours
    ):
        return False
    # Test-and-set: only spawn if we can take the per-workspace lock
    # immediately. A second concurrent caller sees `acquired == False` and
    # bails out without spawning a duplicate daemon.
    lock = get_workspace_lock(workspace_id)
    if not lock.acquire(blocking=False):
        return False
    threading.Thread(
        target=_run_sentinel_pass_safe,
        args=(workspace_id, db_path, settings, embedding_provider, vector_store, lock),
        daemon=True,
        name=f"sentinel-{workspace_id}",
    ).start()
    return True


def _run_sentinel_pass_safe(
    workspace_id: str,
    db_path: Path,
    settings: Settings,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
    lock: threading.Lock,
) -> None:
    """Wrapper that swallows all exceptions so a failed pass never crashes the
    background thread silently — it just logs and moves on. Always releases
    the per-workspace lock so the next overdue tick can fire."""
    try:
        _run_sentinel_pass(
            workspace_id=workspace_id,
            db_path=db_path,
            settings=settings,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
        )
    except Exception:  # pragma: no cover — defensive
        _log.exception("sentinel autorun failed for workspace=%s", workspace_id)
    finally:
        lock.release()


def _run_sentinel_pass(
    *,
    workspace_id: str,
    db_path: Path,
    settings: Settings,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> None:
    discovery = discover_sentinel_file(db_path=db_path)
    cases = load_retrieval_quality_cases(discovery.path) if discovery.path is not None else []
    conn = sqlite3.connect(db_path, timeout=10.0)
    # Row access is the app-wide convention; without it the retrieval-quality
    # eval (run_case) and the brain-pass steps this connection also drives raise
    # "TypeError: tuple indices must be integers or slices, not str" on the first
    # row["col"] access -- which is exactly how this sentinel recorded 19/19
    # 'failed' runs on the live DB before this line existed.
    conn.row_factory = sqlite3.Row
    enable_foreign_keys(conn)  # brain_pass writes are FK-safe (differential-verified)
    try:
        # Cross-workspace guard: if this connection's physical DB is not
        # workspace_id's registered DB, abort before any write. A
        # misrouted (workspace_id, db_path) pair otherwise lets the brain
        # pass write workspace_id's self_model / workspace_meta rows into
        # a foreign DB -- the 2026-05-22 copyBot-into-agent-memory-lite
        # leak. connection_matches_workspace returns True when it cannot
        # tell (unregistered / anchor-colocated / in-memory / registry
        # unreadable) so a freshly bootstrapped workspace is never falsely
        # aborted.
        if not connection_matches_workspace(conn, workspace_id, settings):
            _log.error(
                "sentinel pass aborted: connection %s is not workspace %r's "
                "registered DB -- skipped to prevent cross-workspace pollution",
                db_path,
                workspace_id,
            )
            return
        # Path A: YAML retrieval-quality sentinels (v1.9). Only run when
        # the workspace has a sentinels YAML with cases.
        if cases:
            report = run_retrieval_quality_evals(
                conn,
                workspace_id=workspace_id,
                cases=cases,
                embedding_provider=embedding_provider,
                vector_store=vector_store,
            )
            results = [
                SentinelResultIn(
                    case_name=r.name,
                    status=_to_sentinel_status(r.status),
                    matched_count=len(r.matched_ids),
                    expected_count=len(r.expected_ids),
                    failures=r.failures,
                    metrics=r.metrics,
                )
                for r in report.results
            ]
            record_sentinel_run(conn, workspace_id=workspace_id, results=results)
        # Path B: v3.0.0-final brain maintenance (Phases 1-7). Runs on
        # every overdue tick regardless of YAML presence so newly-
        # bootstrapped workspaces still grow an brain.
        _maybe_run_brain_pass(conn, workspace_id=workspace_id, settings=settings)
        workspace_meta.set_value(conn, workspace_id, _LAST_RUN_KEY, iso_now())
        conn.commit()
    finally:
        conn.close()
