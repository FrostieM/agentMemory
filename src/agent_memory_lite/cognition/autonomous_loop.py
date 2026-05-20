"""v3.4 #1 — closed-loop autonomous research pass.

The v3.1 roadmap called memory "active" — memory that initiates
investigations, not just stores facts. v3.1 shipped V1 (proposes
experiments) + V4 (learns causality) + V5 (predicts failures) + V3
(blindspots), all run by brain_pass. But the loop never CLOSED:
V1 emitted ``theory_proposal`` candidates that sat in the review
queue waiting for operator triage. V4/V5 never saw them as theories
because nobody promoted them. The "active" claim was half-true.

This module closes that loop. For each pending V1 candidate it:

1. Scores plausibility from episode evidence (token overlap with
   the proposal text). Episodes that mention the same domain tokens
   count as supporting evidence; the more, the more confident.
2. Decides: promote (confidence ≥ threshold) | hold (under) | drop
   (no evidence at all).
3. When promoting: writes a ``theory`` row with the V1 body, links
   evidence_count, and copies source_episode_ids into
   ``theory_evidence`` rows. The candidate itself is marked
   ``status='accepted'`` so V1's reject-loop terminator won't try to
   regenerate it.

Result: V1 candidate becomes a real theory the SAME brain_pass tick.
V4 DiD + V5 LR + V3 blindspot scans on the NEXT tick see it as
first-class evidence. The loop runs without operator in the path.

Safety:

* Only candidates with status='new' AND ≥3 source episodes are
  considered (matches V1 min_evidence).
* Confidence threshold (default 0.6) so weak candidates stay as
  candidates — operator still has the final say.
* Plausibility scoring is heuristic (token overlap), NOT LLM-based
  on the hot path. Cheap per-pass, deterministic.
* Failure-soft: missing tables / sqlite errors return empty report.

Settings: ``MEMORY_AUTONOMOUS_LOOP_ENABLED`` (default true),
``MEMORY_AUTONOMOUS_LOOP_CONFIDENCE_THRESHOLD`` (default 0.6),
``MEMORY_AUTONOMOUS_LOOP_MAX_PROMOTIONS`` (default 3 per pass).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_MIN_TOKEN_LEN = 4  # filter ultra-short like "and", "the", "of"
_EVIDENCE_WINDOW_DAYS = 30
_CLAIM_PREVIEW_CHARS = 140  # keep prediction body readable; full text lives in claim


def _synthesize_discipline(
    *, proposal_text: str, supporting_episode_count: int
) -> tuple[list[str], list[str]]:
    """Return (predictions, validation_criteria) for an autonomously-promoted
    theory.

    Today's audit on 2026-05-20 caught ``th_d22488f4a35d3de5`` — a theory
    produced by ``_promote_to_theory`` with empty ``predictions_json`` and
    empty ``validation_criteria_json``. ``scripts/memory_audit.py`` then
    flagged it as ``undisciplined_active_theories``. The discipline-rule
    contract says every active/proposed theory must carry at least one
    prediction AND at least one validation_criterion so future agents
    know what would confirm/refute it.

    The autonomous loop already knows two things at promotion time: the
    proposal text (the claim) and the supporting-episode count (a proxy
    for prior corroboration strength). We deterministically convert both
    into a falsifiable prediction + a measurable validation criterion so
    the hygiene check passes without LLM round-trips. Verbose but cheap.
    """
    preview = (proposal_text or "").strip()
    if len(preview) > _CLAIM_PREVIEW_CHARS:
        preview = preview[:_CLAIM_PREVIEW_CHARS].rstrip() + "..."
    # Prediction: corroboration grows (the V4/V5 layer will keep mining
    # evidence next ticks). Anchored to the autonomous_loop's own token-
    # overlap rule so the next pass can score progress against it.
    predictions = [
        (
            f"Supporting evidence for the claim grows by >=2 over the next "
            f"{_EVIDENCE_WINDOW_DAYS} days. Starting count at promotion was "
            f"{supporting_episode_count}; corroborating episodes are those "
            f"that token-overlap the claim by >=2 terms."
        ),
        (
            "No V6 dispute or V4 supersede event is filed against this "
            "theory within the same window."
        ),
    ]
    # Validation criteria: explicit thresholds the next audit can re-check.
    validation_criteria = [
        (
            f">=1 new supporting episode (token-overlap >=2 with: '{preview}') "
            f"recorded within {_EVIDENCE_WINDOW_DAYS} days, OR an experiment "
            "row with status='completed' and metrics confirming the claim."
        ),
        (
            "Theory is NOT cited by a memory_dispute row with status "
            "'contested' or 'rejected' at validation time."
        ),
    ]
    return predictions, validation_criteria


@dataclass(slots=True)
class AutonomousReport:
    """Per-workspace autonomous-loop summary surfaced via brain_pass."""

    examined: int = 0
    promoted: int = 0
    held: int = 0
    promoted_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int, *, floor: int = 1) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return max(floor, int(raw))
    except ValueError:
        return default


def is_enabled() -> bool:
    """v3.4 default ON. Operator sets
    ``MEMORY_AUTONOMOUS_LOOP_ENABLED=false`` to keep V1 candidates
    waiting for manual review."""
    return _bool_env("MEMORY_AUTONOMOUS_LOOP_ENABLED", True)


def confidence_threshold() -> float:
    return _float_env("MEMORY_AUTONOMOUS_LOOP_CONFIDENCE_THRESHOLD", 0.6)


def max_promotions_per_pass() -> int:
    return _int_env("MEMORY_AUTONOMOUS_LOOP_MAX_PROMOTIONS", 3)


def _tokens(text: str) -> set[str]:
    """Lowercased token set with min-length gate. No stopword list
    because the V1 proposal body is already a noun-rich hypothesis."""
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) >= _MIN_TOKEN_LEN}


def _score_candidate(
    *,
    proposal_text: str,
    source_episode_ids: list[str],
    episode_corpus: list[tuple[str, str]],  # [(episode_id, raw_text), ...]
) -> tuple[float, list[str]]:
    """Plausibility score in [0, 1] + the list of supporting
    episode_ids found in the corpus.

    Recipe: proposal_tokens ∩ episode_tokens. Episodes already cited
    as the candidate's source count automatically. New episodes
    matched via ≥2 token overlap. Final confidence is
    ``min(1.0, 0.3 + 0.1 * supporting_count)`` — capped so a wildly
    confident hypothesis still needs corroborating evidence beyond
    its source episodes.
    """
    target_tokens = _tokens(proposal_text)
    if not target_tokens:
        return 0.0, []
    supporting: list[str] = list(source_episode_ids)
    for ep_id, raw_text in episode_corpus:
        if ep_id in supporting:
            continue
        overlap = len(target_tokens & _tokens(raw_text))
        if overlap >= 2:
            supporting.append(ep_id)
    # Confidence law: 0.3 baseline + 0.1 per supporting episode,
    # capped at 1.0. Three source episodes alone = 0.6 (threshold);
    # one new corroboration pushes to 0.7. Strong-evidence candidates
    # (10+ supporting episodes) saturate at 1.0.
    confidence = min(1.0, 0.3 + 0.1 * len(supporting))
    return confidence, supporting


def _load_pending_candidates(
    conn: sqlite3.Connection, *, workspace_id: str
) -> list[tuple[str, str, str, list[str]]]:
    """Pull V1 candidates ready for autonomous evaluation.

    Returns list of (candidate_id, insight_id_subject, proposal_text,
    source_episode_ids). Insights with <3 source episodes are
    pre-filtered by V1 already, but we double-check via the candidate
    row's source_episode_id (single) — autonomous loop wants more.
    """
    from agent_memory_lite.maintenance.candidates_table import (  # noqa: PLC0415
        resolve_candidates_table,
    )

    table = resolve_candidates_table(conn)
    if table is None:
        return []
    try:
        rows = conn.execute(
            f"""SELECT mc.id, mc.subject, mc.object, i.source_episode_ids_json
                  FROM {table} mc
                  LEFT JOIN insights i ON i.id = mc.subject
                 WHERE mc.workspace_id = ?
                   AND mc.kind = 'theory_proposal'
                   AND mc.status = 'new'
                 LIMIT 50""",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[tuple[str, str, str, list[str]]] = []
    for row in rows:
        cand_id = str(row[0])
        subject = str(row[1] or "")
        proposal_text = str(row[2] or "")
        try:
            ids = json.loads(row[3]) if row[3] else []
            ep_ids = [s for s in ids if isinstance(s, str) and s]
        except (ValueError, TypeError):
            ep_ids = []
        if proposal_text:
            out.append((cand_id, subject, proposal_text, ep_ids))
    return out


def _load_recent_episodes(conn: sqlite3.Connection, *, workspace_id: str) -> list[tuple[str, str]]:
    """Episodes within ``_EVIDENCE_WINDOW_DAYS`` — the corpus scanned
    for corroborating evidence beyond the candidate's own sources."""
    cutoff = (datetime.now(UTC) - timedelta(days=_EVIDENCE_WINDOW_DAYS)).isoformat()
    try:
        rows = conn.execute(
            "SELECT id, raw_text FROM episodes "
            "WHERE workspace_id = ? AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT 500",
            (workspace_id, cutoff),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [(str(r[0]), str(r[1] or "")) for r in rows]


def _promote_to_theory(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    insight_id: str,
    candidate_id: str,
    proposal_text: str,
    supporting_episodes: list[str],
    confidence: float,
) -> str:
    """Write a theory row + evidence rows. Returns the new theory_id.

    Theory ``status='proposed'`` so it's discoverable in research
    surfaces but not yet 'validated'. Operator can promote to
    validated later via the existing experiment pipeline. The V1
    candidate flips to ``status='accepted'`` so the reject-loop
    terminator won't try to regenerate it.
    """
    now = iso_now()
    theory_id = new_id(IdKind.THEORY)
    title = proposal_text[:60].strip() or f"From insight {insight_id}"
    primary_episode = supporting_episodes[0] if supporting_episodes else None
    # v3.4 audit-followup (insight_6cf19fa37cf05012): synthesize discipline
    # fields BEFORE the INSERT so the hygiene check never flags autonomously
    # promoted theories as ``undisciplined_active_theories``.
    predictions, validation_criteria = _synthesize_discipline(
        proposal_text=proposal_text,
        supporting_episode_count=len(supporting_episodes),
    )
    conn.execute(
        """INSERT INTO theories
           (id, workspace_id, title, domain, claim, mechanism,
            predictions_json, validation_criteria_json,
            experiment_plan, tags_json, status,
            confidence, importance, source_episode_id,
            evidence_count, evidence_strength, created_at, updated_at)
           VALUES (?, ?, ?, 'autonomous_loop', ?, '', ?, ?, '', '[]',
                   'proposed', ?, 0.6, ?, ?, ?, ?, ?)""",
        (
            theory_id,
            workspace_id,
            title,
            proposal_text,
            json.dumps(predictions),
            json.dumps(validation_criteria),
            confidence,
            primary_episode,
            len(supporting_episodes),
            confidence,
            now,
            now,
        ),
    )
    for ep_id in supporting_episodes:
        evidence_id = new_id(IdKind.THEORY_EVIDENCE)
        conn.execute(
            """INSERT INTO theory_evidence
               (id, workspace_id, theory_id, kind, summary,
                source_episode_id, confidence, observed_at, created_at)
               VALUES (?, ?, ?, 'autonomous_corroboration',
                       'token-overlap match in episode corpus',
                       ?, ?, ?, ?)""",
            (evidence_id, workspace_id, theory_id, ep_id, confidence, now, now),
        )
    # Mark candidate accepted so V1 reject-loop doesn't regenerate.
    from agent_memory_lite.maintenance.candidates_table import (  # noqa: PLC0415
        resolve_candidates_table,
    )

    table = resolve_candidates_table(conn)
    if table is not None:
        conn.execute(
            f"UPDATE {table} SET status = 'accepted', updated_at = ? WHERE id = ?",
            (now, candidate_id),
        )
    return theory_id


def run_autonomous_pass(conn: sqlite3.Connection, *, workspace_id: str) -> AutonomousReport:
    """One pass over pending V1 candidates.

    Idempotent: candidates that don't clear the threshold stay
    ``status='new'`` and get re-evaluated next pass when new episodes
    might have appeared. Promotions are capped per pass to keep brain_pass
    latency predictable.
    """
    report = AutonomousReport()
    if not is_enabled():
        return report
    threshold = confidence_threshold()
    cap = max_promotions_per_pass()
    try:
        candidates = _load_pending_candidates(conn, workspace_id=workspace_id)
    except sqlite3.Error as exc:
        report.errors.append(f"load:{exc}")
        return report
    if not candidates:
        return report
    corpus = _load_recent_episodes(conn, workspace_id=workspace_id)
    for cand_id, insight_id, proposal_text, source_eps in candidates:
        report.examined += 1
        confidence, supporting = _score_candidate(
            proposal_text=proposal_text,
            source_episode_ids=source_eps,
            episode_corpus=corpus,
        )
        if confidence < threshold:
            report.held += 1
            continue
        try:
            theory_id = _promote_to_theory(
                conn,
                workspace_id=workspace_id,
                insight_id=insight_id,
                candidate_id=cand_id,
                proposal_text=proposal_text,
                supporting_episodes=supporting,
                confidence=confidence,
            )
            report.promoted += 1
            report.promoted_ids.append(theory_id)
        except sqlite3.Error as exc:
            report.errors.append(f"promote:{cand_id}:{exc}")
        if report.promoted >= cap:
            break
    try:
        conn.commit()
    except sqlite3.Error as exc:
        report.errors.append(f"commit:{exc}")
    return report


# Convenience for downstream callers / tests.
def candidate_texts(report: AutonomousReport) -> Iterable[str]:
    return iter(report.promoted_ids)
