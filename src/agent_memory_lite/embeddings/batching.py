"""Batch helper.

Splits an arbitrary list of texts into batches of `batch_size`. The provider
itself handles model-specific timeouts and retries; this helper just slices.
"""

from __future__ import annotations

from collections.abc import Iterator


def iter_batches(texts: list[str], batch_size: int) -> Iterator[list[str]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    for start in range(0, len(texts), batch_size):
        yield texts[start : start + batch_size]
