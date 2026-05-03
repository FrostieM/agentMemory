"""Walk the ``decisions.supersedes_decision_id`` chain.

Returns the chain newest-first (so the first item is the leaf — usually the
currently-active decision — and the last item is the original ancestor).
The walker is bounded by ``max_depth`` to defend against accidental cycles
in the supersede graph; if a cycle is detected the walk stops gracefully
and reports it via a flag in the result.

Confidence trend is computed across the chain: positive means the chain
was getting more confident over time (each supersede strengthened the
position), negative means the position has weakened.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

DEFAULT_MAX_DEPTH = 32


@dataclass(frozen=True, slots=True)
class LineageNode:
    id: str
    title: str
    status: str
    confidence: float
    valid_from: str
    valid_to: str | None
    supersedes_decision_id: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "confidence": self.confidence,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "supersedes_decision_id": self.supersedes_decision_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class DecisionLineage:
    decision_id: str
    chain: list[LineageNode]
    cycle_detected: bool = False
    truncated: bool = False
    confidence_trend: float = 0.0
    confidence_first: float | None = None
    confidence_latest: float | None = None
    extras: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "chain": [node.to_dict() for node in self.chain],
            "cycle_detected": self.cycle_detected,
            "truncated": self.truncated,
            "confidence_trend": self.confidence_trend,
            "confidence_first": self.confidence_first,
            "confidence_latest": self.confidence_latest,
        }


def _fetch_node(
    conn: sqlite3.Connection, *, workspace_id: str, decision_id: str
) -> LineageNode | None:
    row = conn.execute(
        """
        SELECT id, title, status, confidence, valid_from, valid_to,
               supersedes_decision_id, created_at, updated_at
        FROM decisions
        WHERE id = ? AND workspace_id = ?
        """,
        (decision_id, workspace_id),
    ).fetchone()
    if row is None:
        return None
    return LineageNode(
        id=str(row["id"]),
        title=str(row["title"]),
        status=str(row["status"]),
        confidence=float(row["confidence"]),
        valid_from=str(row["valid_from"]),
        valid_to=str(row["valid_to"]) if row["valid_to"] else None,
        supersedes_decision_id=(
            str(row["supersedes_decision_id"]) if row["supersedes_decision_id"] else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def walk_lineage(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    decision_id: str,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> DecisionLineage:
    """Walk newest -> oldest along supersedes_decision_id."""
    seen: set[str] = set()
    chain: list[LineageNode] = []
    cursor_id: str | None = decision_id
    cycle_detected = False
    truncated = False
    while cursor_id is not None:
        if cursor_id in seen:
            cycle_detected = True
            break
        if len(chain) >= max_depth:
            truncated = True
            break
        node = _fetch_node(conn, workspace_id=workspace_id, decision_id=cursor_id)
        if node is None:
            break
        seen.add(cursor_id)
        chain.append(node)
        cursor_id = node.supersedes_decision_id
    return DecisionLineage(
        decision_id=decision_id,
        chain=chain,
        cycle_detected=cycle_detected,
        truncated=truncated,
        confidence_trend=_compute_trend(chain),
        confidence_first=chain[-1].confidence if chain else None,
        confidence_latest=chain[0].confidence if chain else None,
    )


def _compute_trend(chain: list[LineageNode]) -> float:
    """Trend = latest - first. Positive means the chain strengthened over
    time, negative means it weakened. Zero when the chain is empty or has
    one node."""
    if len(chain) < 2:
        return 0.0
    return chain[0].confidence - chain[-1].confidence
