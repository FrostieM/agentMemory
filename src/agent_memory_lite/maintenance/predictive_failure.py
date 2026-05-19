"""v3.1 Vector 5 MVP — predictive failure detection.

Per ``docs/V3_1_BREAKTHROUGH_ROADMAP.md`` Vector 5, memory anticipates
failure: it scans active decisions, finds pairs ``(failed, fresh)``
where the fresh one token-overlaps a known low-outcome failure, and
emits a ``predictive_warning`` so the operator sees the pattern before
the second decision repeats the first's mistake.

# MVP scope

Heuristic (no LLM): Jaccard similarity over the active-decision text
tokens (re-uses ``capability_suggester._tokenize`` + blindspot
opaque-id filter so row-id mentions don't dominate). A pair surfaces
as a warning when:

* ``failed`` has ``outcome_score <= MEMORY_PREDICTIVE_FAILURE_OUTCOME_THRESHOLD``
* ``fresh`` has ``outcome_score`` in the unrated band (|score| < 0.15)
* ``fresh.updated_at > failed.updated_at`` (chronology — we warn
  forward, never backward)
* Jaccard(failed_tokens, fresh_tokens) >= MIN_JACCARD

Output is a dataclass; persistence is the caller's choice (matches
Vector 1 two-step contract). LLM-augmented body generation lands in
the next milestone.

Settings:

* ``MEMORY_PREDICTIVE_FAILURE_ENABLED`` — default ``true``.
* ``MEMORY_PREDICTIVE_FAILURE_OUTCOME_THRESHOLD`` — default ``-0.3``.
* ``MEMORY_PREDICTIVE_FAILURE_JACCARD_MIN`` — default ``0.4``.
* ``MEMORY_PREDICTIVE_FAILURE_LIMIT`` — default ``5``.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from agent_memory_lite.ingestion.capability_suggester import _tokenize
from agent_memory_lite.maintenance.blindspot_filters import is_opaque_id


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
    return _bool_env("MEMORY_PREDICTIVE_FAILURE_ENABLED", True)


def outcome_threshold() -> float:
    return _float_env("MEMORY_PREDICTIVE_FAILURE_OUTCOME_THRESHOLD", -0.3)


def jaccard_min() -> float:
    return _float_env("MEMORY_PREDICTIVE_FAILURE_JACCARD_MIN", 0.4)


def emit_limit() -> int:
    return _int_env("MEMORY_PREDICTIVE_FAILURE_LIMIT", 5)


@dataclass(frozen=True, slots=True)
class PredictiveWarning:
    """One predictive-failure pairing.

    ``similarity`` is the Jaccard overlap of cleaned tokens (opaque
    ids stripped) between the failed decision and the fresh one. The
    operator can pin a behaviour instruction, archive the fresh
    decision, or reject the warning.
    """

    fresh_decision_id: str
    failed_decision_id: str
    similarity: float
    reason_text: str


def _filtered_tokens(text: str) -> set[str]:
    return {t for t in _tokenize(text) if not is_opaque_id(t)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _row_text(row: sqlite3.Row | tuple[object, ...]) -> str:
    """Stitch title + decision_text + rationale for tokenization."""
    if isinstance(row, sqlite3.Row):
        parts = [row["title"], row["decision_text"], row["rationale"]]
    else:
        parts = [row[1], row[2], row[3]]
    return " ".join(str(p) for p in parts if p)


# Vector5-audit-2 M5: Jaccard over very small token sets is noise.
# Require both sides to have at least 4 cleaned tokens before scoring.
_MIN_TOKEN_SET = 4


def find_predictive_warnings(
    conn: sqlite3.Connection, *, workspace_id: str, limit: int | None = None
) -> list[PredictiveWarning]:
    """Scan active decisions for failure-pattern lookalikes.

    Vector5-audit-2 H1: filter NULL updated_at in SQL so the
    chronology guard never gets a sentinel string.
    Vector5-audit-2 H2: respect bi-temporal ``valid_to`` (Phase 6
    invariant — expired decisions don't surface).
    """
    if not is_enabled():
        return []
    cap = limit if limit is not None else emit_limit()
    threshold = outcome_threshold()
    min_j = jaccard_min()
    from agent_memory_lite.utils.time import iso_now  # noqa: PLC0415

    now = iso_now()
    rows = conn.execute(
        "SELECT id, title, decision_text, rationale, "
        "COALESCE(outcome_score, 0.0) AS oscore, updated_at "
        "FROM decisions "
        "WHERE workspace_id = ? AND status = 'active' "
        "AND updated_at IS NOT NULL "
        "AND (valid_to IS NULL OR valid_to > ?) "
        "ORDER BY updated_at DESC",
        (workspace_id, now),
    ).fetchall()
    failed: list[tuple[str, set[str], float, str]] = []
    fresh: list[tuple[str, set[str], str]] = []
    for row in rows:
        rid = str(row[0] if not isinstance(row, sqlite3.Row) else row["id"])
        oscore = float(row[4] if not isinstance(row, sqlite3.Row) else row["oscore"])
        # SQL already filtered NULL updated_at — safe to cast directly.
        upd = str(row[5] if not isinstance(row, sqlite3.Row) else row["updated_at"])
        tokens = _filtered_tokens(_row_text(row))
        if oscore <= threshold:
            failed.append((rid, tokens, oscore, upd))
        elif abs(oscore) < 0.15:
            fresh.append((rid, tokens, upd))
    out: list[PredictiveWarning] = []
    for f_id, f_tokens, f_upd in fresh:
        if len(f_tokens) < _MIN_TOKEN_SET:
            continue  # Vector5-audit-2 M5: too few tokens — Jaccard is noise.
        best: tuple[str, float, float] | None = None
        for fail_id, fail_tokens, fail_score, fail_upd in failed:
            if len(fail_tokens) < _MIN_TOKEN_SET:
                continue
            if fail_upd >= f_upd:
                continue  # we only warn forward — old fresh, newer failure not informative
            sim = _jaccard(f_tokens, fail_tokens)
            if sim >= min_j and (best is None or sim > best[1]):
                best = (fail_id, sim, fail_score)
        if best is not None:
            fail_id, sim, fail_score = best
            out.append(
                PredictiveWarning(
                    fresh_decision_id=f_id,
                    failed_decision_id=fail_id,
                    similarity=sim,
                    reason_text=(
                        f"Decision {f_id} token-overlaps decision {fail_id} "
                        f"(jaccard={sim:.2f}) which has outcome_score={fail_score:.2f}. "
                        f"Review before re-applying the pattern."
                    ),
                )
            )
    out.sort(key=lambda w: w.similarity, reverse=True)
    return out[:cap]
