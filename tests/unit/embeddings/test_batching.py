from __future__ import annotations

import pytest

from agent_memory_lite.embeddings.batching import iter_batches


def test_iter_batches_splits_evenly() -> None:
    assert list(iter_batches(["a", "b", "c", "d"], batch_size=2)) == [["a", "b"], ["c", "d"]]


def test_iter_batches_handles_remainder() -> None:
    assert list(iter_batches(["a", "b", "c"], batch_size=2)) == [["a", "b"], ["c"]]


def test_iter_batches_empty_input() -> None:
    assert list(iter_batches([], batch_size=4)) == []


def test_iter_batches_rejects_non_positive_size() -> None:
    with pytest.raises(ValueError, match="batch_size must be positive"):
        list(iter_batches(["a"], batch_size=0))
