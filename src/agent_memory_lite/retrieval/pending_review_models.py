"""Pydantic-free dataclasses for pending_review surface.

Split out so loaders can import models without circular import on
``pending_review`` (which itself depends on the loaders).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PendingReviewItem:
    kind: str  # "decision_candidate" / "insight_candidate" / "correction_candidate"
    id: str
    title: str
    extra: str  # short hint (theory_id / insight_type / promote endpoint suggestion)


@dataclass(frozen=True, slots=True)
class PendingReviewSummary:
    decision_candidates_count: int
    insight_candidates_count: int
    correction_candidates_count: int = 0
    items: list[PendingReviewItem] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # frozen dataclass + default_factory edge case workaround
        if self.items is None:
            object.__setattr__(self, "items", [])

    @property
    def total(self) -> int:
        return (
            self.decision_candidates_count
            + self.insight_candidates_count
            + self.correction_candidates_count
        )

    def is_empty(self) -> bool:
        return self.total == 0
