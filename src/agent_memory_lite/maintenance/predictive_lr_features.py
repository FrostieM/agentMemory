"""v3.3 Vector 5 — feature extraction for the LR predictor.

Lifted out of ``predictive_lr`` to keep that module under the
150-SLOC ceiling. Pure functions over decision rows + audit events;
no SQL here.

Feature vector layout (deterministic, persisted with the model):

* ``vocab[i]``      — 1 if token i present in the decision title +
                       text + rationale, else 0. ``vocab`` is the
                       per-workspace top-K most-frequent tokens
                       observed at training time. K = ``VOCAB_SIZE``.
* extra slot ``[K]`` — normalized recent-outcome trend (avg of the
                       last ``TREND_WINDOW`` decisions' outcome_score
                       in [-1, 1], rescaled to [0, 1]).
* extra slot ``[K+1]`` — normalized decision trail length (number
                       of decisions in the last 7 days, clipped to
                       ``MAX_TRAIL`` then divided).

Output is plain ``list[float]`` so the LR can dot-product without
numpy. The companion model stores the same vocab so train/predict
operate on aligned indices.
"""

from __future__ import annotations

import re
from collections import Counter

VOCAB_SIZE = 50
TREND_WINDOW = 10
MAX_TRAIL = 20
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_MIN_TOKEN_LEN = 3
_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "have",
        "was",
        "are",
        "but",
        "not",
        "into",
        "via",
        "had",
        "use",
        "any",
        "all",
    }
)


def tokenize(text: str) -> set[str]:
    """Lowercase regex tokenization with stopword filter."""
    return {
        tok.lower()
        for tok in _TOKEN_RE.findall(text or "")
        if len(tok) >= _MIN_TOKEN_LEN and tok.lower() not in _STOP
    }


def build_vocab(texts: list[str], *, size: int = VOCAB_SIZE) -> list[str]:
    """Pick the top-``size`` tokens by document frequency over ``texts``.

    Deterministic: ties broken alphabetically so a retrain on the same
    corpus produces the same vocab → the model's weight ordering stays
    stable across runs.
    """
    df: Counter[str] = Counter()
    for text in texts:
        for tok in tokenize(text):
            df[tok] += 1
    items = sorted(df.items(), key=lambda kv: (-kv[1], kv[0]))
    return [tok for tok, _ in items[:size]]


def featurize(
    *,
    text: str,
    vocab: list[str],
    recent_outcomes: list[float],
    trail_length: int,
) -> list[float]:
    """Build one feature vector from a candidate decision.

    ``recent_outcomes`` is the rolling list of outcome_score values
    for the workspace's last ``TREND_WINDOW`` decisions (oldest first).
    ``trail_length`` is the count of decisions in the recent past
    (training pass picks a 7-day window, but the caller decides).
    """
    tokens = tokenize(text)
    bow = [1.0 if tok in tokens else 0.0 for tok in vocab]
    # Trend slot: avg of recent outcomes (in [-1, 1]) rescaled to [0, 1].
    avg = sum(recent_outcomes) / len(recent_outcomes) if recent_outcomes else 0.0
    trend = max(0.0, min(1.0, (avg + 1.0) / 2.0))
    # Trail slot: clip and normalize.
    trail = min(trail_length, MAX_TRAIL) / MAX_TRAIL
    return [*bow, trend, trail]


def feature_dim(vocab: list[str]) -> int:
    """Total feature count given a vocab — bow + 2 extras."""
    return len(vocab) + 2
