"""v3.1 Vector 1 MVP — memory proposes experiments (heuristic, no LLM).

Per ``docs/V3_1_BREAKTHROUGH_ROADMAP.md`` Vector 1, memory shifts from
reactive to initiating: it scans uncertain insights and proposes
proto-theories with pre-filled validation criteria.

Two-step contract:

* ``find_proposal_candidates`` — DISCOVERY (pure read). Scans
  uncertain insights and returns ``ExperimentProposal`` dataclasses.
* ``persist_proposal`` — PERSISTENCE. Writes one proposal as a
  ``memory_candidate(kind='theory_proposal')`` row. Idempotent via
  deterministic id. The HTTP route runs both in a single pass.

LLM body generation is the next milestone under the same surface.

Min-length gate (Vector1-audit M3) drops whitespace-only summaries
before they become useless "(no summary)" candidates.

Settings: ``MEMORY_EXPERIMENT_PROPOSAL_ENABLED`` (default true),
``..._CONF_MIN`` (0.4 inclusive), ``..._CONF_MAX`` (0.7 inclusive),
``..._LIMIT`` (3 per pass).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.maintenance.experiment_proposal_config import (
    conf_window,
    emit_limit,
    is_enabled,
)
from agent_memory_lite.maintenance.experiment_proposal_writer import persist_proposal

__all__ = [
    "ExperimentProposal",
    "conf_window",
    "emit_limit",
    "find_proposal_candidates",
    "is_enabled",
    "persist_proposal",
]


@dataclass(frozen=True, slots=True)
class ExperimentProposal:
    """One proto-theory candidate emitted by the proposal scanner.

    ``source_episode_id`` carries the first evidence-episode id from the
    underlying insight when available — it satisfies the
    ``memory_candidates.source_episode_id`` FK on persist. Empty when
    the insight has no recorded evidence episodes (persist_proposal
    skips those rather than fabricating an episode).
    """

    insight_id: str
    proposal_text: str
    validation_criterion: str
    confidence: float
    source_episode_id: str = ""


def _proposal_from_insight(
    *,
    insight_id: str,
    summary: str,
    confidence: float,
    source_episode_id: str = "",
) -> ExperimentProposal:
    """Synthesize one ExperimentProposal — LLM-augmented when enabled,
    heuristic fallback otherwise (see ``experiment_proposal_body``)."""
    from agent_memory_lite.maintenance.experiment_proposal_body import (  # noqa: PLC0415
        heuristic_body,
        try_llm_body,
    )

    text = summary.strip() or "(no summary)"
    body = try_llm_body(insight_id, text, confidence) or heuristic_body(
        insight_id, text, confidence
    )
    return ExperimentProposal(
        insight_id=insight_id,
        proposal_text=body[0],
        validation_criterion=body[1],
        confidence=confidence,
        source_episode_id=source_episode_id,
    )


_MIN_SUMMARY_LEN = 8  # Vector1-audit M3: drop whitespace-only / trivial summaries.


def _is_auto_event_noise(summary: str) -> bool:
    """Live-validation-2026-05-20: skip insights dominated by auto-event
    tokens (e.g. ``file_indexed`` from pre-commit ingest). The
    consolidation pass distills recurring themes from EVERY episode,
    so workspaces with active code-memory ingestion get insights like
    "Recurring theme (N episodes): file_indexed, src, ..." which V1
    then dresses up as falsifiable hypotheses — polished noise.

    Filter mirrors the V3 blindspot fix: reuse the same exclusion list
    so operator config is consistent. An insight is "auto-event noise"
    iff one of the excluded source_types appears as a literal token
    in the summary.
    """
    from agent_memory_lite.maintenance.blindspot_filters import (  # noqa: PLC0415
        excluded_source_types,
    )

    haystack = summary.lower()
    return any(name.lower() in haystack for name in excluded_source_types())


def _first_source_episode(raw_json: str | None) -> str:
    """Parse ``insights.source_episode_ids_json`` and return the first
    id (or "" if list is empty / malformed / non-string element).

    Vector1-audit M6: an upstream bug that stores ``[{"id": "ep_1"}]``
    would otherwise pass through as ``"{'id': 'ep_1'}"`` and explode
    the FK constraint downstream. Reject non-string elements here.
    """
    if not raw_json:
        return ""
    try:
        import json as _json  # noqa: PLC0415

        ids = _json.loads(raw_json)
        if isinstance(ids, list) and ids:
            first = ids[0]
            if isinstance(first, str) and first:
                return first
    except (ValueError, TypeError):
        pass
    return ""


def _excluded_insight_ids(conn: sqlite3.Connection, *, workspace_id: str) -> set[str]:
    """Return insight ids whose candidate is already accepted/rejected.

    v3.2 reject-loop termination: ``persist_proposal`` writes a row
    with deterministic id ``cand_prop_<insight_id>`` and gates ON
    CONFLICT updates by ``status='new'``, so an operator-rejected row
    can never silently flip back. BUT the scanner still re-finds the
    same insight every brain_pass and pays for an Ollama call to
    regenerate the body (which the writer then no-ops away). Worse,
    the GET /memory/propose_experiments preview surfaces the
    "regenerated" proposal as if it were fresh, so the operator sees
    a rejected proposal reappear minutes after the reject.

    Cheap fix: skip insights that already have a non-'new' candidate
    in this workspace. Saves the LLM cost, stops the UI bounce-back,
    and preserves the audit trail (rejected candidate stays in DB).

    Failure-soft: when the candidates table doesn't exist
    (pre-migration DB), return empty set so the scanner still runs.
    """
    from agent_memory_lite.maintenance.candidates_table import (  # noqa: PLC0415
        resolve_candidates_table,
    )

    table = resolve_candidates_table(conn)
    if table is None:
        return set()
    try:
        rows = conn.execute(
            f"SELECT subject FROM {table} "
            f"WHERE workspace_id = ? AND kind = 'theory_proposal' "
            f"AND status IN ('accepted', 'rejected') AND subject IS NOT NULL",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return set()
    out: set[str] = set()
    for row in rows:
        sid = row["subject"] if isinstance(row, sqlite3.Row) else row[0]
        if isinstance(sid, str) and sid:
            out.add(sid)
    return out


def find_proposal_candidates(
    conn: sqlite3.Connection, *, workspace_id: str, limit: int | None = None
) -> list[ExperimentProposal]:
    """Scan uncertain insights → proto-theory proposals.

    Returns at most ``limit`` rows (default
    ``MEMORY_EXPERIMENT_PROPOSAL_LIMIT``). Empty list when the feature
    is disabled or no insight clears the confidence window.

    v3.2 reject-loop termination: insights whose deterministic
    candidate id (``cand_prop_<insight_id>``) is already
    accepted/rejected are skipped so the LLM is not re-called and the
    UI does not surface a "fresh" copy of a rejected proposal.
    """
    if not is_enabled():
        return []
    cap = limit if limit is not None else emit_limit()
    lo, hi = conf_window()
    excluded = _excluded_insight_ids(conn, workspace_id=workspace_id)
    # Vector1-audit H2: do NOT swallow OperationalError here. The
    # ``insights`` table missing is a misconfiguration the operator
    # needs to see. ``brain_pass._step_experiment_proposal`` wraps the
    # call with its own ``except (sqlite3.Error, Exception)`` and
    # surfaces the error into the maintenance report.
    # v3.2: SELECT 2*cap so we still have ``cap`` candidates left after
    # the rejected/accepted filter prunes some. Cheap — insights table
    # is small + already filtered by confidence + status='candidate'.
    select_limit = cap * 2 if excluded else cap
    rows = conn.execute(
        "SELECT id, summary, gist, confidence, source_episode_ids_json "
        "FROM insights "
        "WHERE workspace_id = ? AND status = 'candidate' "
        "AND COALESCE(confidence, 0.0) >= ? "
        "AND COALESCE(confidence, 0.0) <= ? "
        "ORDER BY confidence DESC LIMIT ?",
        (workspace_id, lo, hi, select_limit),
    ).fetchall()
    out: list[ExperimentProposal] = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            summary = str(row["gist"] or row["summary"] or "")
            insight_id = str(row["id"])
            confidence = float(row["confidence"] or 0.0)
            source_episode_id = _first_source_episode(row["source_episode_ids_json"])
        else:
            insight_id = str(row[0])
            summary = str(row[2] or row[1] or "")
            confidence = float(row[3] or 0.0)
            source_episode_id = _first_source_episode(row[4] if len(row) > 4 else None)
        # v3.2 reject-loop termination: drop insights whose candidate
        # was already triaged. Saves LLM call + UI bounce-back.
        if insight_id in excluded:
            continue
        # Vector1-audit M3: strip + min-length gate to keep
        # whitespace-only summaries out of memory_candidates.
        if len(summary.strip()) < _MIN_SUMMARY_LEN:
            continue
        # Live-validation-2026-05-20: skip auto-event-noise insights.
        # See _is_auto_event_noise — operator config (env-tunable) is
        # shared with the V3 blindspot filter so both vectors agree.
        if _is_auto_event_noise(summary):
            continue
        out.append(
            _proposal_from_insight(
                insight_id=insight_id,
                summary=summary,
                confidence=confidence,
                source_episode_id=source_episode_id,
            )
        )
        if len(out) >= cap:
            break
    return out
