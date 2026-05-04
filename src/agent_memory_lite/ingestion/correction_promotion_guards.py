"""Pre-promote guard helpers for v1.10 correction promotion.

Split out of ``correction_promotion.py`` to keep the orchestrator
under the 150-SLOC ceiling. Each helper raises
``CorrectionPromotionError`` with a stable ``code`` so the HTTP route
and MCP handler can map to the right error surface.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.enums import MemoryCandidateKind


class CorrectionPromotionError(ValueError):
    """Raised when the candidate isn't promotable.

    The HTTP route translates ``code`` to 404 / 409 / 400. MCP
    surfaces the message verbatim. Stable codes:

      * ``not_found``       — candidate id missing in the workspace
      * ``already_decided`` — candidate.status != 'new'
      * ``wrong_kind``      — candidate.kind != 'correction'
      * ``name_taken``      — active behavior_instruction with same name
    """

    def __init__(self, *, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def load_pending_correction(
    conn: sqlite3.Connection, *, workspace_id: str, candidate_id: str
) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM memory_candidates WHERE id = ? AND workspace_id = ?",
        (candidate_id, workspace_id),
    ).fetchone()
    if row is None:
        raise CorrectionPromotionError(
            code="not_found", detail=f"candidate {candidate_id!r} not found"
        )
    if row["status"] != "new":
        raise CorrectionPromotionError(
            code="already_decided",
            detail=f"candidate already decided (status={row['status']!r})",
        )
    if str(row["kind"]) != MemoryCandidateKind.CORRECTION.value:
        raise CorrectionPromotionError(
            code="wrong_kind",
            detail=(
                f"candidate kind={row['kind']!r} cannot promote to "
                "behavior_instruction; only CORRECTION candidates "
                "are eligible for this endpoint."
            ),
        )
    return row


def coerce_applies_to(value: list[str] | str | None) -> tuple[str, ...]:
    """Single string → 1-tuple; list → tuple of str; None/other → empty.
    Defends against ``tuple("a,b")`` splitting character-by-character —
    used by both the HTTP route and the MCP tool wrappers."""
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(x) for x in value)
    return ()


def guard_name_taken(
    conn: sqlite3.Connection, *, workspace_id: str, name: str, overwrite: bool
) -> None:
    """Block promotion when an active behavior_instruction with the same
    name exists in this workspace, unless ``overwrite=True``."""
    if overwrite:
        return
    existing = conn.execute(
        "SELECT id FROM behavior_instructions "
        "WHERE workspace_id = ? AND name = ? AND COALESCE(active, 1) = 1",
        (workspace_id, name),
    ).fetchone()
    if existing is None:
        return
    raise CorrectionPromotionError(
        code="name_taken",
        detail=(
            f"behavior_instruction with name {name!r} already "
            "exists in this workspace; pass overwrite=true to replace it."
        ),
    )
