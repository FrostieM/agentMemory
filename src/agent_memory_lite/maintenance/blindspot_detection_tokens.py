"""Blindspot model + tokenization primitive.

Extracted from ``blindspot_detection`` to keep that module under the
150-SLOC ceiling. Holds the ``Blindspot`` result dataclass and the
``_tokens_from`` per-episode tokenization helper. Both are re-exported
from ``blindspot_detection`` so existing import paths keep working.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_memory_lite.ingestion.capability_suggester import _tokenize
from agent_memory_lite.maintenance.blindspot_bigrams import (
    bigram_tokens as _bigram_tokens,
)
from agent_memory_lite.maintenance.blindspot_bigrams import (
    is_bigrams_enabled,
)


@dataclass(frozen=True, slots=True)
class Blindspot:
    """One structural blindspot surfaced by the scanner.

    ``description`` is populated by the optional LLM augmentation when
    ``MEMORY_BLINDSPOT_LLM_ENABLED=true`` and Ollama is reachable.
    Empty string by default — heuristic-only callers see the same
    surface as before.
    """

    token: str
    episode_count: int
    decision_count: int  # always 0 by construction, included for clarity
    description: str = ""


def _tokens_from(text: str, *, cap: int) -> set[str]:
    """Tokenize ``text`` with the project's standard stopword filter,
    then truncate to the first ``cap`` distinct tokens. Set semantics
    so an episode counts as 1 toward each unique token regardless of
    how many times the token appears inside it.

    v3.3: when ``MEMORY_BLINDSPOT_BIGRAMS_ENABLED=true`` (default),
    the token set includes bigrams alongside unigrams so multi-word
    concepts ('kelly sizing', 'tier ladder') surface as cohesive
    blindspots instead of fragmenting into useless components.

    Audit-round-3 fix: ``set`` iteration order depends on Python's
    hash randomization → the truncated cap was non-deterministic across
    process restarts → blindspot recall jittered between runs. Sort
    deterministically before slicing.
    """
    tokens = _bigram_tokens(text) if is_bigrams_enabled() else _tokenize(text)
    if not tokens or cap >= len(tokens):
        return tokens
    return set(sorted(tokens)[:cap])
