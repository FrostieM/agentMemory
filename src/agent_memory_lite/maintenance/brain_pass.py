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
its own settings flag -- a failure in one step never blocks the next:
outcome-score recompute, Hebbian soft-edge distillation, insight
promotion, reflex-rule distillation, self-model refresh, causal-link
extraction, DB hygiene (WAL checkpoint + periodic VACUUM), orphan-vector
prune (v3.7 "sleep cleaning"), experiment proposal, predictive-failure
scan, DiD + Granger causality, predictive-LR training, drift sentinel,
and dead-behavior auto-archive. ``MEMORY_BRAIN_PASS_ENABLED`` is the
master switch for the whole path.

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
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
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
            "errors": list(self.errors),
        }


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

        report.outcome_updated = refresh_workspace(conn, workspace_id=workspace_id, now_iso=now_iso)
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
    # by its own env flag (default off); failure-soft on any provider /
    # schema issue.
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
    _step_outcome(conn, workspace_id, started, settings, report)
    _step_hebbian(conn, workspace_id, started, settings, report)
    _step_promote_insights(conn, workspace_id, settings, report)
    _step_reflex_distill(conn, workspace_id, settings, report)
    _step_self_model(conn, workspace_id, settings, report)
    _step_causal(conn, workspace_id, settings, report)
    _step_db_hygiene(conn, workspace_id, report)
    _step_prune_vectors(conn, workspace_id, settings, report)
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
    # Digest refresh commits internally (per upsert), so it runs BEFORE
    # _step_feed_plan_outcomes, whose stamp+bumps must share the single final
    # commit -- keeping that step the last writer before run_brain_pass commits.
    _step_refresh_digests(conn, workspace_id, settings, report)
    _step_feed_plan_outcomes(conn, workspace_id, settings, report)
    try:
        conn.commit()
    except sqlite3.Error as exc:
        report.errors.append(f"final_commit:{exc}")
    report.finished_at = iso_now()
    if report.errors:
        _log.warning(
            "brain_pass partial failure workspace=%s errors=%s",
            workspace_id,
            ";".join(report.errors),
        )
        # Round-2 audit (M1): the BrainPassReport is discarded by the
        # sentinel scheduler — a permanently-failing step is otherwise
        # invisible (only this log line). Persist a maintenance_event so
        # /health and the hygiene report surface a brain step that is
        # silently broken every tick. Failure-soft: observability
        # writing must never abort the pass.
        try:
            from agent_memory_lite.ingestion.maintenance_writer import (  # noqa: PLC0415
                write_maintenance_event,
            )
            from agent_memory_lite.models.enums import MaintenanceSeverity  # noqa: PLC0415
            from agent_memory_lite.models.maintenance import MaintenanceEventIn  # noqa: PLC0415

            write_maintenance_event(
                conn,
                MaintenanceEventIn(
                    workspace_id=workspace_id,
                    kind="brain_pass_step_failed",
                    severity=MaintenanceSeverity.WARNING,
                    summary=f"{len(report.errors)} brain-pass step(s) failed this tick",
                    details={"errors": report.errors[:20]},
                ),
            )
        except Exception:  # pragma: no cover - observability is best-effort
            pass
    return report
