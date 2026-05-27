"""Pydantic-free dataclasses for pending_review surface.

Split out so loaders can import models without circular import on
``pending_review`` (which itself depends on the loaders).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PendingReviewItem:
    kind: str
    id: str
    title: str
    extra: str


@dataclass(frozen=True, slots=True)
class PendingReviewSummary:
    decision_review_count: int
    insight_review_count: int
    correction_review_count: int = 0
    items: list[PendingReviewItem] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # frozen dataclass + default_factory edge case workaround
        if self.items is None:
            object.__setattr__(self, "items", [])

    @property
    def total(self) -> int:
        return self.decision_review_count + self.insight_review_count + self.correction_review_count

    def is_empty(self) -> bool:
        return self.total == 0
