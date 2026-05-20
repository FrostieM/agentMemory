"""v3.3 — bigram + named-entity-aware tokenizer for V3 blindspot detection.

Current V3 surfaces unigrams ('compiled', 'kelly'). Live-2026-05-20
audit on copyBot found that the real blindspots are multi-word
concepts ('kelly sizing', 'tier ladder', 'boot gate filter') and
compound identifiers ('refreshExitConfigs', 'maker_bot') — the
unigram view fragments them into useless components.

This module emits both unigrams AND bigrams so the cluster surface
can highlight cohesive phrases. Bigrams that are mostly stopwords or
opaque-id halves get filtered. Compound identifiers (snake_case,
CamelCase) survive as-is from the underlying regex.

Failure-soft: same return shape as the legacy
``capability_suggester._tokenize`` (a ``set[str]``) so the caller can
opt in / out without conditional handling logic.
"""

from __future__ import annotations

import itertools
import os
import re

from agent_memory_lite.ingestion._capability_suggester_stopwords import STOPWORDS as _STOPWORDS

# ``\w+`` already preserves snake_case ('boot_gate', 'tier_ladder') and
# CamelCase ('RefreshExitConfigs') as a single token. We only add the
# *ordered-pair* layer on top.
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_MIN_TOKEN_LEN = 3
_MIN_BIGRAM_LEN = 7  # "kelly sizing" = 12 chars; "ab cd" = 5 chars (skip).


def is_bigrams_enabled() -> bool:
    """v3.3: bigrams default ON. Set
    ``MEMORY_BLINDSPOT_BIGRAMS_ENABLED=false`` to restore the v3.1
    unigram-only behavior for byte-equivalent rollback."""
    raw = os.environ.get("MEMORY_BLINDSPOT_BIGRAMS_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _raw_tokens(text: str) -> list[str]:
    """Lowercase regex tokenization with stopword + min-length filter.

    Returns a LIST (not set) so the bigram pass can see token order.
    """
    return [
        tok.lower()
        for tok in _TOKEN_RE.findall(text or "")
        if len(tok) >= _MIN_TOKEN_LEN and tok.lower() not in _STOPWORDS
    ]


def _looks_like_opaque_id(token: str) -> bool:
    """Inline check for opaque ids (ep_*, dec_*, ins_*, cand_*, cm_*,
    pure hex). Mirrors the canonical filter in ``blindspot_filters`` so
    we don't surface 'ep_abc123' as a fake bigram half."""
    if "_" in token:
        prefix = token.split("_", 1)[0]
        if prefix in {"ep", "dec", "ins", "cand", "cm", "chk", "ws"}:
            return True
    # Pure-hex check: token is 12+ chars and only hex digits.
    return len(token) >= 12 and all(c in "0123456789abcdef" for c in token)


def bigram_tokens(text: str) -> set[str]:
    """Return unigrams plus bigrams (space-joined), filtered + capped.

    Bigrams are emitted only when:
    * neither half is an opaque id,
    * the joined string is at least ``_MIN_BIGRAM_LEN`` chars (so we
      don't pollute the set with 'ab cd' shrapnel),
    * at least one half is informative (i.e. NOT in the stopword set
      — but since ``_raw_tokens`` already drops stopwords, this falls
      out for free).
    """
    raw = _raw_tokens(text)
    if not raw:
        return set()
    out: set[str] = set()
    for tok in raw:
        if not _looks_like_opaque_id(tok):
            out.add(tok)
    for left, right in itertools.pairwise(raw):
        if _looks_like_opaque_id(left) or _looks_like_opaque_id(right):
            continue
        bigram = f"{left} {right}"
        if len(bigram) < _MIN_BIGRAM_LEN:
            continue
        out.add(bigram)
    return out


def is_compound_identifier(token: str) -> bool:
    """v3.3 NER boost signal: a token containing an underscore or mixed
    case is more likely to be a domain-specific concept (snake_case
    function name, CamelCase class, qualified path) than a plain word.
    Used by the blindspot ranker to break ties — among tokens with the
    same episode_count, compound identifiers float to the top."""
    if "_" in token:
        return True
    # Reject all-lower / all-upper / digits-only — keep mixed case only.
    has_lower = any(c.islower() for c in token)
    has_upper = any(c.isupper() for c in token)
    return has_lower and has_upper
