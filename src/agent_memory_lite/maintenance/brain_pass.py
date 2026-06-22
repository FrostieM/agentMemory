"""Run the memory-brain maintenance loops for one workspace.

This module is the single integration point that keeps the brain alive
between sessions. It runs from the existing trigger-on-traffic
sentinel_scheduler -- no separate cron, no Task Scheduler entry, no
operator-run scripts. Whenever a compact read fires (or the in-process
MCP local fallback runs), the scheduler checks
``MEMORY_SENTINEL_AUTORUN_HOURS`` and, if overdue, spawns a daemon
thread that runs both the YAML retrieval-quality sentinels AND this
brain pass.

The pass is a sequence of independent, idempotent steps, each gated by
its own settings flag -- a failure in one step never blocks the next.
Grouped by purpose (the exact set evolves; see ``run_brain_pass`` for the
authoritative order):
  * learning -- outcome-score recompute, Hebbian soft-edge distillation,
    insight promotion, reflex-rule distillation, self-model refresh,
    causal extraction (primary + embedding + DiD + Granger), autonomous
    candidate->theory promotion, predictive-failure scan + predictive-LR
    training, experiment proposal;
  * storage hygiene -- DB WAL checkpoint + periodic VACUUM/ANALYZE,
    opt-in row retention, orphan-vector prune + LanceDB compaction +
    missing-vector repair, aged sibling-backup reaping;
  * plan learning -- playbook distillation, plan-outcome maturity,
    capability-confidence recompute, code-digest refresh;
  * observability -- drift sentinel, dead-behavior auto-archive.
The per-step ``BrainPassReport`` carries the row counts.
``MEMORY_BRAIN_PASS_ENABLED`` is the master switch for the whole path.

The whole pass is wrapped so that any uncaught exception simply
swallows + logs; the next overdue tick retries. Returns a small
report for telemetry / hygiene dashboards.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.utils.time import iso_now

_log = logging.getLogger(__name__)


@contextlib.contextmanager
def _immediate_tx(conn: sqlite3.Connection) -> Iterator[None]:
    """BEGIN IMMEDIATE / COMMIT around a write loop — but a no-op when
    the connection is ALREADY inside a transaction.

    Round-2 audit (M2): the per-step persist loops issued a raw
    ``conn.execute("BEGIN IMMEDIATE")``. If an earlier brain_pass step
    left a transaction open, that raw BEGIN raises
    ``OperationalError: cannot start a transaction within a
    transaction`` and the step's ``except: ROLLBACK`` then discards the
    OUTER transaction's uncommitted work. When already nested, the
    writes simply ride the outer transaction and the outer owner
    commits. IMMEDIATE is kept for the un-nested case to acquire the
    write lock up front (avoids 'database is locked' against a
    concurrent MCP persist call)."""
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


@dataclass(slots=True)
class BrainPassReport:
    """Per-workspace brain-maintenance summary."""

    workspace_id: str
    started_at: str
    finished_at: str
    # feedback_ewma rows refreshed from memory_usage_feedback this pass (the
    # input the outcome step reads). Recomputed before _step_outcome.
    feedback_ewma_recomputed: int = 0
    outcome_updated: dict[str, int] = field(default_factory=dict)
    hebbian_edges_upserted: int = 0
    hebbian_edges_gated: int = 0
    hebbian_rows_pruned: int = 0
    insights_promoted: int = 0
    insights_skipped: int = 0
    reflex_rules_distilled: int = 0
    self_model_refreshed: bool = False
    causal_invalidated: int = 0
    causal_derived: int = 0
    wal_checkpoint_pages: int = 0
    vacuum_ran: bool = False
    # v3.6 Phase-2: orphan chunk-vectors deleted this pass (sleep cleaning).
    vectors_pruned: int = 0
    # Plan 7: chunks whose vector was missing (embedding_id IS NULL) re-embedded
    # this pass -- the self-healing complement to orphan-prune.
    vectors_repaired: int = 0
    # Phase-4 row retention (opt-in; default OFF): rows age-pruned this pass from
    # the four unbounded tables.
    audit_rows_pruned: int = 0
    usage_feedback_pruned: int = 0
    retention_causal_pruned: int = 0
    file_episodes_pruned: int = 0
    # Phase-4: aged sibling DB-snapshot backups reaped this pass.
    backups_pruned: int = 0
    # Single-observation, stale CODE edges pruned from soft_edges this pass
    # (the read-dead bloat in the #1 table). Memory edges are never touched.
    soft_edges_pruned: int = 0
    experiment_proposals: int = 0
    predictive_warnings: int = 0
    # Vector5-audit-2 H4: explicit "schema missing" telemetry so a
    # legacy-only deploy looks different from "feature ran clean".
    predictive_warnings_available: bool = True
    # v3.2 dead-behavior auto-archive count (rows flipped active=1 -> 0).
    behaviors_auto_archived: int = 0
    # v3.3 Vector 4 method (a): DiD causal links emitted from supersede pairs.
    causal_did_pairs_scanned: int = 0
    causal_did_links_emitted: int = 0
    # v3.4 #7 Vector 4 method (b): Granger-style lead-lag causality on
    # memory_usage_feedback daily activity. pairs_scanned counts ordered
    # (X, Y) pairs that cleared the min-activity gate; links_emitted
    # counts NEW granger_caused rows landed this tick.
    causal_granger_pairs_scanned: int = 0
    causal_granger_links_emitted: int = 0
    # v3.3 Vector 5 LR: per-pass training outcome.
    predictive_lr_trained: bool = False
    predictive_lr_samples: int = 0
    # v3.4 drift sentinel telemetry.
    drift_findings: list[str] = field(default_factory=list)
    drift_resolved: list[str] = field(default_factory=list)
    # v3.4 #1 autonomous loop — closes V1→theory→V4/V5 cycle.
    autonomous_examined: int = 0
    autonomous_promoted: int = 0
    autonomous_held: int = 0
    # Phase 5b: completed plans distilled into playbooks this pass.
    plan_playbooks_distilled: int = 0
    # Phase 5c: (plan step -> capability) outcomes fed into maturity this pass.
    plan_step_outcomes_fed: int = 0
    # Plan 10.5: durable code-digest refresh -- bounded re-verify of existing
    # digests so impact_check staleness self-heals between full audits.
    digests_checked: int = 0
    digests_refreshed: int = 0
    # Plan #121: capability maturity -> confidence recompute (rows scanned and
    # rows whose stored confidence actually moved this pass).
    capability_confidence_scanned: int = 0
    capability_confidence_updated: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "feedback_ewma_recomputed": self.feedback_ewma_recomputed,
            "outcome_updated": dict(self.outcome_updated),
            "hebbian_edges_upserted": self.hebbian_edges_upserted,
            "hebbian_edges_gated": self.hebbian_edges_gated,
            "hebbian_rows_pruned": self.hebbian_rows_pruned,
            "insights_promoted": self.insights_promoted,
            "insights_skipped": self.insights_skipped,
            "reflex_rules_distilled": self.reflex_rules_distilled,
            "self_model_refreshed": self.self_model_refreshed,
            "causal_invalidated": self.causal_invalidated,
            "causal_derived": self.causal_derived,
            "wal_checkpoint_pages": self.wal_checkpoint_pages,
            "vacuum_ran": self.vacuum_ran,
            "vectors_pruned": self.vectors_pruned,
            "vectors_repaired": self.vectors_repaired,
            "audit_rows_pruned": self.audit_rows_pruned,
            "usage_feedback_pruned": self.usage_feedback_pruned,
            "retention_causal_pruned": self.retention_causal_pruned,
            "file_episodes_pruned": self.file_episodes_pruned,
            "backups_pruned": self.backups_pruned,
            "soft_edges_pruned": self.soft_edges_pruned,
            "experiment_proposals": self.experiment_proposals,
            "predictive_warnings": self.predictive_warnings,
            "predictive_warnings_available": self.predictive_warnings_available,
            "behaviors_auto_archived": self.behaviors_auto_archived,
            "causal_did_pairs_scanned": self.causal_did_pairs_scanned,
            "causal_did_links_emitted": self.causal_did_links_emitted,
            "causal_granger_pairs_scanned": self.causal_granger_pairs_scanned,
            "causal_granger_links_emitted": self.causal_granger_links_emitted,
            "predictive_lr_trained": self.predictive_lr_trained,
            "predictive_lr_samples": self.predictive_lr_samples,
            "drift_findings": list(self.drift_findings),
            "drift_resolved": list(self.drift_resolved),
            "autonomous_examined": self.autonomous_examined,
            "autonomous_promoted": self.autonomous_promoted,
            "autonomous_held": self.autonomous_held,
            "plan_playbooks_distilled": self.plan_playbooks_distilled,
            "plan_step_outcomes_fed": self.plan_step_outcomes_fed,
            "digests_checked": self.digests_checked,
            "digests_refreshed": self.digests_refreshed,
            "capability_confidence_scanned": self.capability_confidence_scanned,
            "capability_confidence_updated": self.capability_confidence_updated,
            "errors": list(self.errors),
        }


def _step_feedback_ewma(
    conn: sqlite3.Connection,
    workspace_id: str,
    settings: Settings,
    report: BrainPassReport,
) -> None:
    """Recompute feedback_ewma from memory_usage_feedback for chunk/decision/theory
    BEFORE the outcome step reads it. Previously the recompute ran ONLY via the
    /feedback_summary HTTP route, so on a maintenance-driven deployment
    feedback_ewma stayed 0 and decisions/theories could never earn a
    feedback-driven outcome_score. Gated + failure-soft."""
    if not settings.feedback_ewma_enabled:
        return
    try:
        from agent_memory_lite.retrieval.feedback_aggregator import (  # noqa: PLC0415
            SUPPORTED_SOURCE_TYPES,
            recompute_workspace_ewma,
        )

        total = 0
        for source_type in SUPPORTED_SOURCE_TYPES:
            results = recompute_workspace_ewma(
                conn,
                workspace_id=workspace_id,
                source_type=source_type,
                half_life_days=settings.feedback_halflife_days,
                exclude_self_loop=settings.feedback_exclude_self_loop,
                max_per_day_per_source=settings.feedback_max_per_day_per_source,
            )
            total += len(results)
        report.feedback_ewma_recomputed = total
        conn.commit()
    except Exception as exc:
        report.errors.append(f"feedback_ewma:{exc}")


def _step_outcome(
    conn: sqlite3.Connection,
    workspace_id: str,
    now_iso: str,
    settings: Settings,
    report: BrainPassReport,
) -> None:
    if not settings.outcome_loop_enabled:
        return
    try:
        from agent_memory_lite.cognition.outcome_recompute import (  # noqa: PLC0415
            refresh_workspace,
        )

        report.outcome_updated = refresh_workspace(
            conn,
            workspace_id=workspace_id,
            now_iso=now_iso,
            decision_feedback_cap=settings.feedback_max_per_day_per_source,
            decision_exclude_self_loop=settings.feedback_exclude_self_loop,
        )
        conn.commit()
    except Exception as exc:
        report.errors.append(f"outcome:{exc}")


def _step_hebbian(
    conn: sqlite3.Connection,
    workspace_id: str,
    now_iso: str,
    settings: Settings,
    report: BrainPassReport,
) -> None:
    if not settings.hebbian_enabled:
        return
    try:
        from agent_memory_lite.maintenance.hebbian_pass import distill_workspace  # noqa: PLC0415
        from agent_memory_lite.retrieval.coactivation_log import prune_log  # noqa: PLC0415

        upserted, gated = distill_workspace(
            conn,
            workspace_id=workspace_id,
            outcome_gate=settings.hebbian_outcome_gate,
        )
        pruned = prune_log(
            conn,
            workspace_id=workspace_id,
            retention_days=settings.hebbian_log_retention_days,
            now_iso=now_iso,
        )
        report.hebbian_edges_upserted = upserted
        report.hebbian_edges_gated = gated
        report.hebbian_rows_pruned = pruned
    except Exception as exc:
        report.errors.append(f"hebbian:{exc}")


def _step_promote_insights(
    conn: sqlite3.Connection,
    workspace_id: str,
    settings: Settings,
    report: BrainPassReport,
) -> None:
    if not settings.consolidation_feedback_enabled:
        return
    try:
        from agent_memory_lite.compaction.promote_insight_to_behavior import (  # noqa: PLC0415
            promote_eligible_insights,
        )

        stats = promote_eligible_insights(conn, workspace_id=workspace_id)
        report.insights_promoted = stats.promoted
        report.insights_skipped = stats.skipped
    except Exception as exc:
        report.errors.append(f"promote:{exc}")


def _step_reflex_distill(
    conn: sqlite3.Connection,
    workspace_id: str,
    settings: Settings,
    report: BrainPassReport,
) -> None:
    if not settings.reflex_enabled:
        return
    try:
        # Aliased so it doesn't collide with maintenance.hebbian_pass.distill_workspace.
        from agent_memory_lite.enforcement.reflex_distiller import (  # noqa: PLC0415
            distill_workspace as _reflex_distill_workspace,
        )

        result = _reflex_distill_workspace(
            conn,
            workspace_id=workspace_id,
            min_support=settings.reflex_distiller_min_support,
        )
        report.reflex_rules_distilled = result.rules_upserted
    except Exception as exc:
        report.errors.append(f"reflex_distill:{exc}")


def _step_self_model(
    conn: sqlite3.Connection,
    workspace_id: str,
    settings: Settings,
    report: BrainPassReport,
) -> None:
    if not settings.self_model_enabled:
        return
    try:
        from agent_memory_lite.cognition.self_model import refresh_self_model  # noqa: PLC0415

        ollama_url = settings.llm_base_url if settings.self_model_ollama else None
        ollama_model = settings.llm_model if settings.self_model_ollama else None
        model = refresh_self_model(
            conn,
            workspace_id=workspace_id,
            ollama_base_url=ollama_url,
            ollama_model=ollama_model,
        )
        report.self_model_refreshed = model is not None
    except Exception as exc:
        report.errors.append(f"self_model:{exc}")


def _step_causal(
    conn: sqlite3.Connection,
    workspace_id: str,
    settings: Settings,
    report: BrainPassReport,
) -> None:
    if not settings.recall_enabled:
        return
    try:
        from agent_memory_lite.retrieval.causal_extractor import extract_workspace  # noqa: PLC0415

        cr = extract_workspace(conn, workspace_id=workspace_id)
        report.causal_invalidated = cr.invalidated_links
        report.causal_derived = cr.derived_links
    except Exception as exc:
        report.errors.append(f"causal:{exc}")
    # v3.1 Vector 4: derive embedding-based semantic causal links. Gated
    # by its own env flag (default on since 2026-05-20); failure-soft on any
    # provider / schema issue.
    try:
        from agent_memory_lite.retrieval.causal_embedding import derive_workspace  # noqa: PLC0415

        derived_n = derive_workspace(conn, workspace_id=workspace_id)
        report.causal_derived += derived_n
    except Exception as exc:
        report.errors.append(f"causal_embedding:{exc}")


def _step_db_hygiene(conn: sqlite3.Connection, workspace_id: str, report: BrainPassReport) -> None:
    """WAL checkpoint every tick + periodic VACUUM. Failure-soft."""
    try:
        from agent_memory_lite.maintenance.db_hygiene import run_db_hygiene  # noqa: PLC0415

        hygiene = run_db_hygiene(conn, workspace_id=workspace_id)
        report.wal_checkpoint_pages = hygiene.wal_checkpoint_pages
        report.vacuum_ran = hygiene.vacuum_ran
        if hygiene.errors:
            report.errors.extend(f"db_hygiene:{e}" for e in hygiene.errors)
    except Exception as exc:
        report.errors.append(f"db_hygiene:{exc}")


def _step_prune_vectors(
    conn: sqlite3.Connection,
    workspace_id: str,
    settings: Settings,
    report: BrainPassReport,
) -> None:
    """Delete orphan chunk-vectors -- vectors whose chunk row is gone.

    The "sleep cleaning" loop. SQLite and LanceDB have no cross-store
    transaction, so chunk deletes / interrupted compound writes leave
    vectors with no backing chunk -- dead weight that wastes top-K
    search slots. Failure-soft like every other step."""
    if not settings.vector_prune_enabled:
        return
    try:
        from agent_memory_lite.maintenance.vector_prune import (  # noqa: PLC0415
            prune_orphan_vectors,
        )
        from agent_memory_lite.vector_store.factory import (  # noqa: PLC0415
            get_vector_store,
        )

        store = get_vector_store(settings)
        try:
            report.vectors_pruned = prune_orphan_vectors(
                conn,
                store,
                workspace_id=workspace_id,
                max_delete=settings.vector_prune_max_per_pass,
            )
        finally:
            store.close()
    except Exception as exc:
        report.errors.append(f"prune_vectors:{exc}")


_LAST_VECTOR_COMPACT_KEY = "last_vector_compact_at"
_VECTOR_COMPACT_INTERVAL_HOURS = 24.0


def _step_compact_vectors(
    conn: sqlite3.Connection,
    workspace_id: str,
    settings: Settings,
    report: BrainPassReport,
) -> None:
    """Collapse LanceDB MVCC version history (the prune step just appended more).

    Every upsert / delete / orphan-prune appends a manifest version that nothing
    else removes -- measured 4282 versions / 340 MB for 7356 live rows.
    ``store.compact()`` runs ``Table.optimize``, recovering ~96% of the on-disk
    size. Heavier than the prune, so rate-limited to once per day via
    workspace_meta and gated by the same ``vector_prune_enabled`` flag. The
    default 1h compaction window is safe against a concurrent in-process writer.
    Failure-soft like every other step."""
    if not settings.vector_prune_enabled:
        return
    try:
        from datetime import UTC, datetime, timedelta  # noqa: PLC0415

        from agent_memory_lite.config import workspace_meta  # noqa: PLC0415
        from agent_memory_lite.utils.time import parse_iso  # noqa: PLC0415
        from agent_memory_lite.vector_store.factory import (  # noqa: PLC0415
            get_vector_store,
        )

        last = workspace_meta.get(conn, workspace_id, _LAST_VECTOR_COMPACT_KEY)
        if last:
            with contextlib.suppress(ValueError):
                if datetime.now(UTC) - parse_iso(last) < timedelta(
                    hours=_VECTOR_COMPACT_INTERVAL_HOURS
                ):
                    return
        store = get_vector_store(settings)
        try:
            compact = getattr(store, "compact", None)
            if callable(compact):
                result = compact()  # default 1h window -- safe vs a concurrent writer
                # Surface a permanently-broken compaction (every namespace -1)
                # instead of silently re-stamping and retrying only once a day.
                if result and all(v == -1 for v in result.values()):
                    report.errors.append(f"compact_vectors:all {len(result)} namespaces failed")
        finally:
            store.close()
        workspace_meta.set_value(conn, workspace_id, _LAST_VECTOR_COMPACT_KEY, iso_now())
        conn.commit()
    except Exception as exc:
        report.errors.append(f"compact_vectors:{exc}")


def _step_retention(
    conn: sqlite3.Connection,
    workspace_id: str,
    now_iso: str,
    settings: Settings,
    report: BrainPassReport,
) -> None:
    """Phase-4: age-prune the four unbounded tables (audit_log,
    memory_usage_feedback, re-derived causal_links, superseded file_indexed
    episodes). The first data-DELETING brain step -- OPT-IN (default OFF); every
    prune is self-guarded + bounded. Failure-soft."""
    if not settings.retention_enabled:
        return
    try:
        from agent_memory_lite.maintenance.row_retention import prune_retention  # noqa: PLC0415

        result = prune_retention(
            conn,
            workspace_id=workspace_id,
            settings=settings,
            now_iso=now_iso,
            cap=settings.retention_max_per_table_per_pass,
        )
        report.audit_rows_pruned = result.audit_rows_pruned
        report.usage_feedback_pruned = result.usage_feedback_pruned
        report.retention_causal_pruned = result.causal_links_pruned
        report.file_episodes_pruned = result.file_episodes_pruned
        report.errors.extend(f"retention:{e}" for e in result.errors)
    except Exception as exc:
        report.errors.append(f"retention:{exc}")


def _step_soft_edge_prune(
    conn: sqlite3.Connection,
    workspace_id: str,
    now_iso: str,
    settings: Settings,
    report: BrainPassReport,
) -> None:
    """Prune single-observation, stale CODE edges from soft_edges -- the read-dead
    bloat in the #1 table by row count. Default ON (non-destructive vs anything
    read, like the backup reaper); the MEMORY edges (co_retrieved / co_mentioned)
    that feed recall + the brief are NEVER touched. Bounded + failure-soft."""
    if not settings.soft_edge_prune_enabled:
        return
    try:
        from agent_memory_lite.maintenance.soft_edge_prune import (  # noqa: PLC0415
            prune_soft_edges,
        )

        result = prune_soft_edges(
            conn,
            workspace_id=workspace_id,
            max_observation_count=settings.soft_edge_prune_max_observation_count,
            min_age_days=settings.soft_edge_prune_min_age_days,
            cap=settings.soft_edge_prune_max_per_pass,
            now_iso=now_iso,
        )
        report.soft_edges_pruned = result.pruned
        if result.error:
            report.errors.append(f"soft_edge_prune:{result.error}")
    except Exception as exc:
        report.errors.append(f"soft_edge_prune:{exc}")


_LAST_BACKUP_PRUNE_KEY = "last_backup_prune_at"
_BACKUP_PRUNE_INTERVAL_HOURS = 24.0


def _step_prune_backups(
    conn: sqlite3.Connection,
    workspace_id: str,
    settings: Settings,
    report: BrainPassReport,
) -> None:
    """Phase-4: reap aged sibling DB-snapshot backups the repair scripts leave
    next to the live DB with no retention (the multi-GB bloat source). Keep-N
    floor + age gate, anchored to the live DB filename so the live store is never
    touched. Rate-limited once/24h via workspace_meta. Failure-soft; structural
    housekeeping, so a failure is WARNING-level (not in _CORE_STEP_PREFIXES)."""
    if not settings.backup_retention_enabled:
        return
    try:
        from datetime import UTC, datetime, timedelta  # noqa: PLC0415

        from agent_memory_lite.config import workspace_meta  # noqa: PLC0415
        from agent_memory_lite.maintenance.backup_retention import (  # noqa: PLC0415
            prune_sibling_db_backups,
        )
        from agent_memory_lite.utils.time import parse_iso  # noqa: PLC0415

        last = workspace_meta.get(conn, workspace_id, _LAST_BACKUP_PRUNE_KEY)
        if last:
            with contextlib.suppress(ValueError):
                if datetime.now(UTC) - parse_iso(last) < timedelta(
                    hours=_BACKUP_PRUNE_INTERVAL_HOURS
                ):
                    return
        # Stamp before the sweep so a persistently-failing prune is rate-limited
        # to one WARNING per 24h instead of every tick (the sweep is cheap +
        # idempotent, so deferring a transient failure for a day is harmless).
        workspace_meta.set_value(conn, workspace_id, _LAST_BACKUP_PRUNE_KEY, iso_now())
        conn.commit()
        deleted = prune_sibling_db_backups(
            settings.db_path,
            keep=settings.backup_retention_keep,
            max_age_days=settings.backup_retention_age_days,
        )
        report.backups_pruned = len(deleted)
    except Exception as exc:
        report.errors.append(f"prune_backups:{exc}")


def _step_repair_missing_vectors(
    conn: sqlite3.Connection,
    workspace_id: str,
    settings: Settings,
    report: BrainPassReport,
) -> None:
    """Plan 7: re-embed a bounded batch of chunks whose vector never landed
    (``embedding_id IS NULL``), so a missing vector self-heals incrementally
    instead of waiting for a full operator reindex. The inverse of
    ``_step_prune_vectors`` (orphan vectors). Cost discipline: a cheap EXISTS
    probe runs first, and the heavy embedding provider + vector store are
    acquired ONLY when there is real work, so the steady state (no missing
    vectors) costs one indexed query (idx_chunks_missing_embedding) and never
    loads the model. Failure-soft."""
    if not settings.vector_repair_enabled:
        return
    try:
        probe = conn.execute(
            # ORDER BY created_at steers SQLite onto the idx_chunks_missing_embedding
            # partial index (which carries created_at) instead of the broader
            # idx_chunks_archived, so an empty probe is O(1) not O(workspace).
            "SELECT 1 FROM chunks WHERE workspace_id = ? "
            "AND embedding_id IS NULL AND is_archived = 0 ORDER BY created_at LIMIT 1",
            (workspace_id,),
        ).fetchone()
        if probe is None:
            return
        from agent_memory_lite.embeddings.factory import (  # noqa: PLC0415
            get_embedding_provider,
        )
        from agent_memory_lite.vector_store.factory import (  # noqa: PLC0415
            get_vector_store,
        )
        from agent_memory_lite.vector_store.reindex import (  # noqa: PLC0415
            repair_missing_vectors,
        )

        store = get_vector_store(settings)
        try:
            report.vectors_repaired = repair_missing_vectors(
                conn,
                workspace_id=workspace_id,
                provider=get_embedding_provider(settings),
                store=store,
                limit=settings.vector_repair_max_per_pass,
            )
        finally:
            store.close()
    except Exception as exc:
        report.errors.append(f"vector_repair:{exc}")


def _step_experiment_proposal(
    conn: sqlite3.Connection, workspace_id: str, report: BrainPassReport
) -> None:
    """v3.1 Vector 1 MVP. Scan uncertain insights and persist each
    proposal as a ``memory_candidate(kind=theory_proposal)`` row so it
    surfaces in the existing pending-review queue. Idempotent via
    deterministic candidate id derived from the source insight id.

    Final-audit H2 fix: wrap the persist loop in ``BEGIN IMMEDIATE`` /
    ``COMMIT`` to match the HTTP route + MCP handler — without this,
    a concurrent ``persist=true`` call's ``BEGIN IMMEDIATE`` would
    hit "database is locked" against brain_pass's row-by-row writes.
    """
    try:
        from agent_memory_lite.maintenance.experiment_proposal import (  # noqa: PLC0415
            find_proposal_candidates,
            is_enabled,
            persist_proposal,
        )

        if not is_enabled():
            return
        proposals = find_proposal_candidates(conn, workspace_id=workspace_id)
        report.experiment_proposals = len(proposals)
        if not proposals:
            return
        with _immediate_tx(conn):
            for proposal in proposals:
                persist_proposal(conn, workspace_id=workspace_id, proposal=proposal)
    except Exception as exc:
        report.errors.append(f"experiment_proposal:{exc}")


def _latest_episode_id(conn: sqlite3.Connection, workspace_id: str) -> str:
    """Most-recent episode in the workspace, for warning attribution."""
    row = conn.execute(
        "SELECT id FROM episodes WHERE workspace_id = ? ORDER BY created_at DESC LIMIT 1",
        (workspace_id,),
    ).fetchone()
    if not row:
        return ""
    return str(row[0] if not isinstance(row, sqlite3.Row) else row["id"])


def _step_predictive_failure(
    conn: sqlite3.Connection, workspace_id: str, report: BrainPassReport
) -> None:
    """v3.1 Vector 5 MVP. Scan active decisions for failure-pattern
    lookalikes, tally how many warnings the heuristic surfaces, and
    persist each warning as a ``memory_candidate(kind=predictive_warning)``
    row so it appears in ``/ui/review`` automatically.

    Vector5-audit-2 H4: schema-missing (legacy-only DB w/o
    ``outcome_score`` column) sets ``predictive_warnings_available``
    to ``False`` so dashboards distinguish it from a clean zero.
    """
    try:
        from agent_memory_lite.maintenance.predictive_failure import (  # noqa: PLC0415
            find_predictive_warnings,
            is_enabled,
        )
        from agent_memory_lite.maintenance.predictive_failure_writer import (  # noqa: PLC0415
            persist_warning,
        )

        if not is_enabled():
            return
        warnings = find_predictive_warnings(conn, workspace_id=workspace_id)
        report.predictive_warnings = len(warnings)
        if warnings:
            ep_id = _latest_episode_id(conn, workspace_id)
            if ep_id:
                # _immediate_tx: BEGIN IMMEDIATE around the persist loop
                # to match HTTP/MCP semantics, but a no-op when already
                # nested (Round-2 audit M2).
                with _immediate_tx(conn):
                    for warning in warnings:
                        persist_warning(
                            conn,
                            workspace_id=workspace_id,
                            warning=warning,
                            source_episode_id=ep_id,
                        )
    except sqlite3.OperationalError as exc:
        report.predictive_warnings_available = False
        report.errors.append(f"predictive_failure:{exc}")
    except Exception as exc:
        report.errors.append(f"predictive_failure:{exc}")


def _step_predictive_lr_train(
    conn: sqlite3.Connection, workspace_id: str, now_iso: str, report: BrainPassReport
) -> None:
    """v3.3 Vector 5: retrain the LR classifier on accumulated history.

    Cheap when below min_samples (just a COUNT query); SGD-bounded
    when training. Idempotent: model JSON in workspace_meta is
    replaced on each successful train.
    """
    try:
        from agent_memory_lite.maintenance.predictive_lr import (  # noqa: PLC0415
            is_enabled,
            train_workspace,
        )

        if not is_enabled():
            return
        result = train_workspace(conn, workspace_id=workspace_id, now_iso=now_iso)
        report.predictive_lr_trained = result.trained
        report.predictive_lr_samples = result.samples
        if result.errors:
            report.errors.extend(f"predictive_lr:{e}" for e in result.errors)
    except Exception as exc:
        report.errors.append(f"predictive_lr:{exc}")


def _step_causal_did(conn: sqlite3.Connection, workspace_id: str, report: BrainPassReport) -> None:
    """v3.3 Vector 4 method (a): mine DiD causal links from
    supersede pairs. Failure-soft on missing schema."""
    try:
        from agent_memory_lite.retrieval.causal_did import (  # noqa: PLC0415
            extract_did_links,
            is_enabled,
        )

        if not is_enabled():
            return
        result = extract_did_links(conn, workspace_id=workspace_id)
        report.causal_did_pairs_scanned = result.pairs_scanned
        report.causal_did_links_emitted = result.links_emitted
    except Exception as exc:
        report.errors.append(f"causal_did:{exc}")


def _step_causal_granger(
    conn: sqlite3.Connection, workspace_id: str, report: BrainPassReport
) -> None:
    """v3.4 #7 Vector 4 method (b): lead-lag Granger detector on
    memory_usage_feedback daily activity. Sister method to DiD —
    both drop their links into causal_links so the recall layer can
    read multi-method confirmation as a confidence boost. Granger
    runs AFTER DiD because DiD operates on the smaller supersede
    set and is therefore cheaper to fail-soft if it errors first.
    Failure-soft on missing schema."""
    try:
        from agent_memory_lite.retrieval.causal_granger import (  # noqa: PLC0415
            extract_granger_links,
            is_enabled,
        )

        if not is_enabled():
            return
        result = extract_granger_links(conn, workspace_id=workspace_id)
        report.causal_granger_pairs_scanned = result.pairs_scanned
        report.causal_granger_links_emitted = result.links_emitted
    except Exception as exc:
        report.errors.append(f"causal_granger:{exc}")


def _step_autonomous_loop(
    conn: sqlite3.Connection, workspace_id: str, report: BrainPassReport
) -> None:
    """v3.4 #1 — close V1→theory→V4/V5 cycle.

    Runs AFTER V1 (so fresh proposals are present in the queue) and
    BEFORE V4 DiD / V5 LR (so newly promoted theories feed both on
    the same pass). Failure-soft."""
    try:
        from agent_memory_lite.cognition.autonomous_loop import (  # noqa: PLC0415
            is_enabled,
            run_autonomous_pass,
        )

        if not is_enabled():
            return
        result = run_autonomous_pass(conn, workspace_id=workspace_id)
        report.autonomous_examined = result.examined
        report.autonomous_promoted = result.promoted
        report.autonomous_held = result.held
        if result.errors:
            report.errors.extend(f"autonomous:{e}" for e in result.errors)
    except Exception as exc:
        report.errors.append(f"autonomous:{exc}")


def _step_drift_sentinel(
    conn: sqlite3.Connection, workspace_id: str, report: BrainPassReport
) -> None:
    """v3.4 drift sentinel: detect FK / FTS / vector coverage gaps
    and emit maintenance_events. Resolves stale findings when the
    underlying metrics clear. Failure-soft."""
    try:
        from agent_memory_lite.maintenance.drift_sentinel import (  # noqa: PLC0415
            detect_drift,
            is_enabled,
        )

        if not is_enabled():
            return
        result = detect_drift(conn, workspace_id=workspace_id)
        report.drift_findings = list(result.findings)
        report.drift_resolved = list(result.resolved)
        if result.errors:
            report.errors.extend(f"drift:{e}" for e in result.errors)
    except Exception as exc:
        report.errors.append(f"drift:{exc}")


def _step_behavior_auto_archive(
    conn: sqlite3.Connection,
    workspace_id: str,
    settings: Settings,
    report: BrainPassReport,
) -> None:
    """v3.2: archive never-fired behaviors older than the age threshold."""
    if not settings.behavior_auto_archive_enabled:
        return
    try:
        from agent_memory_lite.maintenance.behavior_auto_archive import (  # noqa: PLC0415
            auto_archive_dead_behaviors,
        )

        result = auto_archive_dead_behaviors(
            conn,
            workspace_id=workspace_id,
            age_days=settings.behavior_auto_archive_age_days,
        )
        report.behaviors_auto_archived = result.archived
    except Exception as exc:
        report.errors.append(f"behavior_auto_archive:{exc}")


def _step_distill_plan_playbooks(
    conn: sqlite3.Connection,
    workspace_id: str,
    settings: Settings,
    report: BrainPassReport,
) -> None:
    """Phase 5b: distil each completed plan into a replayable playbook.

    Idempotent (playbook name ``plan:<task_id>``) + failure-soft like
    every brain-pass step."""
    if not settings.plan_playbook_distill_enabled:
        return
    try:
        from agent_memory_lite.maintenance.plan_playbook_distiller import (  # noqa: PLC0415
            distill_completed_plans,
        )

        report.plan_playbooks_distilled = distill_completed_plans(
            conn,
            workspace_id=workspace_id,
            max_distill=settings.plan_playbook_distill_max_per_pass,
        )
    except Exception as exc:
        report.errors.append(f"plan_playbook_distill:{exc}")


def _step_feed_plan_outcomes(
    conn: sqlite3.Connection,
    workspace_id: str,
    settings: Settings,
    report: BrainPassReport,
) -> None:
    """Phase 5c: feed terminal plan-step outcomes into capability maturity.

    Idempotent (``plan_steps.outcome_fed_at`` marker) + failure-soft like
    every brain-pass step."""
    if not settings.plan_outcome_maturity_enabled:
        return
    try:
        from agent_memory_lite.maintenance.plan_outcome_maturity import (  # noqa: PLC0415
            feed_plan_step_outcomes,
        )

        report.plan_step_outcomes_fed = feed_plan_step_outcomes(
            conn,
            workspace_id=workspace_id,
            max_steps=settings.plan_outcome_maturity_max_per_pass,
        )
    except Exception as exc:
        report.errors.append(f"plan_outcome_maturity:{exc}")


def _step_refresh_digests(
    conn: sqlite3.Connection,
    workspace_id: str,
    settings: Settings,
    report: BrainPassReport,
) -> None:
    """Plan 10.5: re-verify a bounded, rotating batch of code digests and
    recompute the stale ones, so an ``impact_check`` stale verdict self-heals
    without waiting for a full audit. The project root (to read source files)
    is resolved from the workspace registry; a workspace with no registered
    root is a no-op. Failure-soft like every other step."""
    if not settings.digest_refresh_enabled:
        return
    try:
        from agent_memory_lite.config.workspace_registry import WorkspaceRegistry  # noqa: PLC0415
        from agent_memory_lite.maintenance.digest_refresh import (  # noqa: PLC0415
            refresh_stale_digests,
        )

        project_root: Path | None = None
        entry = WorkspaceRegistry(settings.workspaces_file).get(workspace_id)
        if entry is not None and entry.project_root:
            project_root = Path(entry.project_root)
        stats = refresh_stale_digests(
            conn,
            workspace_id=workspace_id,
            project_root=project_root,
            limit=settings.digest_refresh_max_per_pass,
        )
        report.digests_checked = stats.checked
        report.digests_refreshed = stats.refreshed
    except Exception as exc:
        report.errors.append(f"digest_refresh:{exc}")


def _step_recompute_capability_confidence(
    conn: sqlite3.Connection,
    workspace_id: str,
    settings: Settings,
    report: BrainPassReport,
) -> None:
    """Plan #121: feed capability maturity counters into stored confidence.

    Runs AFTER ``_step_feed_plan_outcomes`` so this pass's freshly-recorded
    plan-step outcomes are already in the success/failure counters when the
    curve is applied. It does not commit internally, so feed's stamp+bumps and
    these confidence updates ride the single final commit atomically.
    Idempotent + failure-soft; gated by the maturity master flag."""
    if not settings.capability_maturity_enabled:
        return
    try:
        from agent_memory_lite.maintenance.capability_confidence import (  # noqa: PLC0415
            recompute_capability_confidence,
        )

        stats = recompute_capability_confidence(
            conn,
            workspace_id=workspace_id,
            half_life_days=float(settings.capability_decay_days),
            limit=settings.capability_confidence_max_per_pass,
        )
        report.capability_confidence_scanned = stats.scanned
        report.capability_confidence_updated = stats.updated
    except Exception as exc:
        report.errors.append(f"capability_confidence:{exc}")


# The learning core: the steps that form or score the PRIMARY memory substrate,
# plus final_commit (the transaction boundary -- its failure discards the entire
# pass's writes, the worst outcome of all). Errors are tagged "<prefix>:<exc>", so
# a failed step is identified by the substring before the first colon; when any of
# these fail the pass escalates the maintenance_event from WARNING to ERROR (the
# 2026-05-25 tuple-row incident downed outcome/hebbian/self_model/causal for two
# passes and read no louder than a cosmetic digest-refresh hiccup on /health).
#
# Deliberately EXCLUDED -- their failure degrades a DERIVED or secondary signal,
# not core formation, so they stay WARNING. The axis is WHAT the step produces,
# not whether it is enabled (most excluded steps are on by default):
#   * causal_embedding / causal_did / causal_granger -- extra causal-INFERENCE
#     methods layered on the links the primary `causal` step already extracts;
#   * predictive_failure / predictive_lr -- failure prediction, a derived analytic;
#   * reflex_distill -- distills advisory enforcement reminders (reflex_rules), an
#     enforcement-layer artifact, not scored memory knowledge;
#   * the maintenance / structural steps (db_hygiene, prune_vectors,
#     compact_vectors, vector_repair, digest_refresh, drift, behavior_auto_archive,
#     plan_playbook_distill, plan_outcome_maturity, experiment_proposal,
#     capability_confidence).
_CORE_STEP_PREFIXES = frozenset(
    {
        "outcome",  # decision / insight outcome scoring
        "hebbian",  # co-activation -> associative edges
        "promote",  # insight -> behavior promotion
        "self_model",  # identity narrative refresh
        "causal",  # primary causal-link extraction
        "autonomous",  # candidate -> theory promotion
        "final_commit",  # transaction boundary: failure discards the whole pass
    }
)


def _record_pass_outcome(
    conn: sqlite3.Connection, workspace_id: str, report: BrainPassReport
) -> None:
    """Persist the pass outcome to maintenance_events (failure-soft).

    The sentinel scheduler discards the BrainPassReport, so a permanently
    failing step is otherwise invisible (only a log line). On failure we write
    a ``brain_pass_step_failed`` event; a learning-core failure escalates to
    ERROR (else WARNING). On a fully clean pass we resolve any prior open
    event so a transient blip does not stay red on /health. All paths are
    failure-soft -- observability must never abort the pass.
    """
    if not report.errors:
        # Self-heal: the brain ran clean, so any prior tick's failure has
        # cleared. Resolve open brain_pass_step_failed events for this workspace
        # (mirrors drift_sentinel, which resolves findings when its metric
        # clears) -- otherwise the coalesced ERROR row from a transient core blip
        # would stay permanently red on /health and the escalation would lose its
        # signal. A later recurrence re-inserts a fresh row (the dedup below only
        # matches OPEN events).
        try:
            conn.execute(
                "UPDATE maintenance_events SET status = 'resolved', resolved_at = ? "
                "WHERE workspace_id = ? AND kind = 'brain_pass_step_failed' "
                "AND status = 'open'",
                (report.finished_at, workspace_id),
            )
            conn.commit()
        except sqlite3.Error:  # pragma: no cover - observability is best-effort
            pass
        return

    # A learning-core step failing is qualitatively worse than a peripheral one:
    # the brain has stopped forming/scoring knowledge, not just skipped
    # housekeeping. Identify failures by the "<prefix>:" error tag.
    failed_steps = sorted({e.split(":", 1)[0] for e in report.errors})
    core_failed = sorted(set(failed_steps) & _CORE_STEP_PREFIXES)
    log = _log.error if core_failed else _log.warning
    log(
        "brain_pass partial failure workspace=%s core_failed=%s errors=%s",
        workspace_id,
        ",".join(core_failed) or "-",
        ";".join(report.errors),
    )
    try:
        from agent_memory_lite.ingestion.maintenance_writer import (  # noqa: PLC0415
            write_maintenance_event,
        )
        from agent_memory_lite.models.enums import MaintenanceSeverity  # noqa: PLC0415
        from agent_memory_lite.models.maintenance import MaintenanceEventIn  # noqa: PLC0415

        if core_failed:
            severity = MaintenanceSeverity.ERROR
            summary = (
                f"learning-core step(s) failed this tick: {', '.join(core_failed)} "
                f"({len(report.errors)} error(s) total)"
            )
        else:
            severity = MaintenanceSeverity.WARNING
            summary = f"{len(report.errors)} brain-pass step(s) failed this tick"
        write_maintenance_event(
            conn,
            MaintenanceEventIn(
                workspace_id=workspace_id,
                kind="brain_pass_step_failed",
                severity=severity,
                summary=summary,
                details={
                    "errors": report.errors[:20],
                    "core_failed": core_failed,
                    # Coalesce identical failure signatures. A permanently broken
                    # step otherwise inserts one (now ERROR-severity) row every
                    # tick -- unbounded. write_maintenance_event dedups OPEN events
                    # by this fingerprint, bumping seen_count instead of piling up
                    # rows; a CHANGE in which steps fail yields a new fingerprint,
                    # hence a new event.
                    "fingerprint": "brain_pass_step_failed:" + ",".join(failed_steps),
                },
            ),
        )
    except Exception:  # pragma: no cover - observability is best-effort
        pass


def run_brain_pass(
    conn: sqlite3.Connection, *, workspace_id: str, settings: Settings
) -> BrainPassReport:
    """Run the brain-maintenance steps against one workspace.

    Each step is independent + failure-soft. Returns a report capturing
    per-step row counts so the operator can see whether anything moved.
    """
    # The sentinel scheduler owns a raw sqlite3.connect() path. Brain-pass
    # steps intentionally use row["field"] adapters, so normalize here at
    # the integration boundary instead of requiring every caller to remember
    # the repository connection factory.
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row
    started = iso_now()
    report = BrainPassReport(workspace_id=workspace_id, started_at=started, finished_at=started)
    # Refresh feedback_ewma from the usage-feedback log BEFORE the outcome step,
    # which reads it -- otherwise outcome_score is recomputed from a stale (0) EWMA.
    _step_feedback_ewma(conn, workspace_id, settings, report)
    _step_outcome(conn, workspace_id, started, settings, report)
    _step_hebbian(conn, workspace_id, started, settings, report)
    _step_promote_insights(conn, workspace_id, settings, report)
    _step_reflex_distill(conn, workspace_id, settings, report)
    _step_self_model(conn, workspace_id, settings, report)
    _step_causal(conn, workspace_id, settings, report)
    _step_db_hygiene(conn, workspace_id, report)
    # Row retention runs right after db_hygiene (which owns the weekly VACUUM) and
    # before the vector prune/compact, so audit_log is pruned before the episode
    # guard re-evaluates and the freed SQLite pages are reclaimed by the next VACUUM.
    _step_retention(conn, workspace_id, started, settings, report)
    # soft_edges code-edge prune runs right after row retention (both bound an
    # unbounded table). db_hygiene already ran its weekly-gated VACUUM earlier
    # this pass, so the SQLite pages freed here are reclaimed by the next pass's
    # VACUUM (SQLite reuses them internally in the meantime).
    _step_soft_edge_prune(conn, workspace_id, started, settings, report)
    _step_prune_vectors(conn, workspace_id, settings, report)
    _step_compact_vectors(conn, workspace_id, settings, report)
    _step_prune_backups(conn, workspace_id, settings, report)
    # Plan 7: re-embed missing vectors (inverse of prune). Lazy-loads the
    # provider only when the cheap EXISTS probe finds work.
    _step_repair_missing_vectors(conn, workspace_id, settings, report)
    _step_experiment_proposal(conn, workspace_id, report)
    # v3.4 #1: autonomous loop reads V1 candidates emitted by the
    # step above and promotes the confident ones to theories BEFORE
    # V4 DiD / V5 LR scan, so both pick up the new theories same pass.
    _step_autonomous_loop(conn, workspace_id, report)
    _step_predictive_failure(conn, workspace_id, report)
    _step_causal_did(conn, workspace_id, report)
    _step_causal_granger(conn, workspace_id, report)
    _step_predictive_lr_train(conn, workspace_id, started, report)
    _step_drift_sentinel(conn, workspace_id, report)
    _step_behavior_auto_archive(conn, workspace_id, settings, report)
    _step_distill_plan_playbooks(conn, workspace_id, settings, report)
    # Digest refresh commits internally (per upsert), so it runs BEFORE the two
    # steps below, whose writes must share the single final commit. Plan
    # outcomes are fed first; the maturity->confidence recompute (#121) runs
    # last so it sees this pass's freshly-bumped success/failure counters --
    # neither commits internally, so feed's stamp+bumps and the confidence
    # updates land together in run_brain_pass's final commit.
    _step_refresh_digests(conn, workspace_id, settings, report)
    _step_feed_plan_outcomes(conn, workspace_id, settings, report)
    _step_recompute_capability_confidence(conn, workspace_id, settings, report)
    try:
        conn.commit()
    except sqlite3.Error as exc:
        report.errors.append(f"final_commit:{exc}")
    report.finished_at = iso_now()
    _record_pass_outcome(conn, workspace_id, report)
    return report
