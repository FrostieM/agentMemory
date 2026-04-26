"""Cheap token counting.

The chars/4 heuristic is Anthropic's published rule of thumb and is good enough
for chunk-budget arithmetic. Callers that need accurate counts (e.g. final
context budget enforcement) can swap in a real tokenizer later via
`set_token_estimator`.
"""

from __future__ import annotations

from collections.abc import Callable

TokenEstimator = Callable[[str], int]


def _default_estimator(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


_estimator: TokenEstimator = _default_estimator


def estimate_tokens(text: str) -> int:
    return _estimator(text)


def set_token_estimator(estimator: TokenEstimator) -> None:
    """Test / advanced hook. Production code uses `estimate_tokens`."""
    global _estimator  # noqa: PLW0603
    _estimator = estimator


def reset_token_estimator() -> None:
    set_token_estimator(_default_estimator)
