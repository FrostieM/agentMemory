"""Suggest existing decisions that overlap a new write (Move 5).

Move 3/4 surface ``capability_suggestions`` so the agent sees which
role/skill/playbook to link. Move 5 closes the parallel gap on the
decision side: when an agent writes a new decision, return the top-N
existing active decisions in the same workspace whose title + text +
rationale tokens overlap. The agent then sees "there are already 3
decisions about this topic" and can supersede instead of fragment.

Read-only hint — never auto-supersedes. Trust-gate philosophy
matches Move 3 exactly: server suggests, operator (or agent under
human review) decides.

Implementation reuses ``_overlap_coefficient`` + ``_tokenize`` from
``capability_suggester`` so token semantics stay consistent across
the two suggesters; the only divergence is the source table and the
self-exclusion (a decision must never recommend itself).

Performance bound (Audit 2 #6 fix): unbounded ``SELECT *`` over the
decisions table tokenized every active decision per write. On a
workspace with 1000+ decisions this is O(N) tokenization on every
write — ~100ms in the hot path. The scan is now capped at
``MEMORY_DECISION_NEIGHBOR_CANDIDATES`` (default 200) most-recently
updated active decisions. Recency is the right axis because new
writes tend to overlap conceptually with recent work; if the bottom
800 stale decisions are needed, an explicit ``memory_search`` or
``memory_list`` covers the long tail.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from dataclasses import dataclass

from agent_memory_lite.ingestion.capability_suggester import (
    _overlap_coefficient,
    _tokenize,
)


@dataclass(frozen=True, slots=True)
class DecisionNeighbor:
    decision_id: str
    title: str
    snippet: str
    score: float  # 0.0..1.0 overlap-coefficient
    status: str


def _candidate_cap() -> int:
    """How many most-recent active decisions to tokenize per write.

    Default 200 keeps the hot-path under a few ms even on large
    workspaces. Operator can flip via env. Floor 10 so the suggester
    doesn't become a no-op when someone sets a silly value.
    """
    raw = os.environ.get("MEMORY_DECISION_NEIGHBOR_CANDIDATES", "200").strip()
    try:
        value = int(raw)
    except ValueError:
        return 200
    return max(10, value)


def _decision_text(row: sqlite3.Row) -> str:
    parts = [str(row["title"] or ""), str(row["decision_text"] or "")]
    # rationale is optional in the projection — fall back to empty
    # string when the SELECT didn't include the column.
    with contextlib.suppress(IndexError, KeyError):
        parts.append(str(row["rationale"] or ""))
    return " ".join(p for p in parts if p)


def suggest_decision_neighbors(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    title: str,
    text: str,
    rationale: str | None = None,
    exclude_id: str | None = None,
    limit: int = 3,
    min_score: float = 0.15,
    candidate_cap: int | None = None,
) -> list[DecisionNeighbor]:
    """Top-N active decisions whose tokens overlap the new write.

    ``exclude_id`` filters the just-written row out so the response
    never points the agent at the decision it literally just landed.
    ``min_score`` is higher than the capability suggester (0.10) because
    the decision-to-decision pool is denser and noisier — Sub-0.15 hits
    tend to be coincidental rather than topical.

    ``candidate_cap`` (default ``MEMORY_DECISION_NEIGHBOR_CANDIDATES`` or
    200) bounds the candidate scan to the N most-recently-updated active
    decisions. Older decisions are skipped — if you need the long tail,
    call ``memory_search`` directly.
    """
    target = _tokenize(" ".join(filter(None, [title, text, rationale])))
    if not target:
        return []
    cap = candidate_cap if candidate_cap is not None else _candidate_cap()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, title, decision_text, rationale, status "
            "FROM decisions WHERE workspace_id = ? AND status = 'active' "
            "ORDER BY updated_at DESC LIMIT ?",
            (workspace_id, cap),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[DecisionNeighbor] = []
    for row in rows:
        row_id = str(row["id"])
        if exclude_id is not None and row_id == exclude_id:
            continue
        score = _overlap_coefficient(target, _tokenize(_decision_text(row)))
        if score < min_score:
            continue
        body = str(row["decision_text"] or "")
        out.append(
            DecisionNeighbor(
                decision_id=row_id,
                title=str(row["title"] or ""),
                snippet=body[:120],
                score=round(score, 4),
                status=str(row["status"] or "active"),
            )
        )
    out.sort(key=lambda n: -n.score)
    return out[:limit]
