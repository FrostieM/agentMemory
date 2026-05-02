"""Batch helper.

Splits an arbitrary list into batches of `batch_size`. Generic over the element
type so the same helper works for `list[str]` (provider input) and
`list[tuple[...]]` (reindex pipeline).
"""

from __future__ import annotations

from collections.abc import Iterator


def iter_batches[T](items: list[T], batch_size: int) -> Iterator[list[T]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]
