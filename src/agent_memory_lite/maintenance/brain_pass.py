"""v3.0.0-final: run all 7 memory-brain maintenance loops for one workspace.

This module is the single integration point that keeps the brain alive
between sessions. It runs from the existing trigger-on-traffic
sentinel_scheduler -- no separate cron, no Task Scheduler entry, no
operator-run scripts. Whenever ``/memory/get_context`` fires (or the
in-process MCP local fallback runs), the scheduler checks
``MEMORY_SENTINEL_AUTORUN_HOURS`` and, if overdue, spawns a daemon
thread that runs both the YAML retrieval-quality sentinels AND this
brain pass.

The pass is composed of six idempotent steps, each gated by its
own settings flag. A failure in one step never blocks the next:

  1. Phase 1 -- recompute outcome_score across knowledge tables.
  2. Phase 2 -- distill retrieval_coactivation into soft_edges
                (with HeLa-Mem outcome gate, prune stale log rows).
  3. Phase 3 -- promote insights that crossed the confidence + surface
                gate into pinned behaviors.
  4. Phase 4 -- distill new reflex rules from low-outcome insights.
  5. Phase 5 -- refresh the self-model narrative.
  6. Phase 7 -- extract causal links (supersedes -> invalidated,
                insight -> episode derived_from).

The whole pass is wrapped so that any uncaught exception simply
swallows + logs; the next overdue tick retries. Returns a small
report for telemetry / hygiene dashboards.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.utils.time import iso_now

_log = logging.getLogger(__name__)


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
    except (sqlite3.Error, ImportError) as exc:
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
    except (sqlite3.Error, ImportError) as exc:
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
    except (sqlite3.Error, ImportError) as exc:
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
    except (sqlite3.Error, ImportError) as exc:
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
    except (sqlite3.Error, ImportError) as exc:
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
    except (sqlite3.Error, ImportError) as exc:
        report.errors.append(f"causal:{exc}")


def run_brain_pass(
    conn: sqlite3.Connection, *, workspace_id: str, settings: Settings
) -> BrainPassReport:
    """Run all six brain-maintenance steps against one workspace.

    Each step is independent + failure-soft. Returns a report capturing
    per-step row counts so the operator can see whether anything moved.
    """
    started = iso_now()
    report = BrainPassReport(workspace_id=workspace_id, started_at=started, finished_at=started)
    _step_outcome(conn, workspace_id, started, settings, report)
    _step_hebbian(conn, workspace_id, started, settings, report)
    _step_promote_insights(conn, workspace_id, settings, report)
    _step_reflex_distill(conn, workspace_id, settings, report)
    _step_self_model(conn, workspace_id, settings, report)
    _step_causal(conn, workspace_id, settings, report)
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
    return report
