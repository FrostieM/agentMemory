"""v3.3 Vector 3 last-mile — learned workspace stopwords.

The previous v3.3 commit added bigrams + compound-identifier ranking
to push V3 from 8 to 9. The roadmap also called for
"workspace-specific stopword learning" — and live blindspot output
on copyBot still showed mid-noise unigrams like ``27d`` (a date
string) or ``compiled`` (an action verb that appears in nearly every
deploy episode).

Idea: a token that shows up in a high fraction of episodes is
*not* a blindspot signal — it's workspace-common infrastructure.
Compute the per-workspace token frequency once, cache it, then
subtract those tokens from the candidate set before ranking.

Trade-off threshold: 0.40. Tokens present in ≥40% of recent
episodes are considered workspace-stopwords. Below that, they
might still be a real recurring pattern worth surfacing.

Cache: per-(workspace_id, cutoff_iso) so a brief render inside the
same maintenance window doesn't recompute. Bounded to 8 entries via
LRU eviction on dict insertion order.

Failure-soft: missing ``episodes`` table → empty set; the caller
treats that as "no learned stops to add" and falls back to the
project's static stopword list.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections import Counter
from datetime import UTC, datetime, timedelta

from agent_memory_lite.maintenance.blindspot_bigrams import bigram_tokens
from agent_memory_lite.maintenance.blindspot_filters import excluded_source_types

_STOPS_CACHE: dict[tuple[str, str], frozenset[str]] = {}
_STOPS_CACHE_MAX = 8
# Round-2 audit: same LRU race as cognition.brief._BRIEF_CACHE — eviction
# iterates while mutating, which is unsafe under concurrent inserts from
# the blindspot sentinel + HTTP handlers.
_STOPS_CACHE_LOCK = threading.Lock()


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int, *, floor: int = 1) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return max(floor, int(raw))
    except ValueError:
        return default


def is_enabled() -> bool:
    """v3.3: default ON. Set
    ``MEMORY_BLINDSPOT_LEARNED_STOPS_ENABLED=false`` to disable."""
    return _bool_env("MEMORY_BLINDSPOT_LEARNED_STOPS_ENABLED", True)


def threshold() -> float:
    """Min episode-frequency to qualify as a workspace stopword.

    0.40 catches dates ('27d') and infrastructure verbs ('compiled')
    that appear in nearly every episode in the corpus, while leaving
    real recurring concerns (8-30% of episodes) on the candidate
    list. Lower = more aggressive filter.
    """
    return _float_env("MEMORY_BLINDSPOT_LEARNED_STOPS_THRESHOLD", 0.40)


def min_corpus_size() -> int:
    """Below this many episodes the workspace is too small for
    statistical learning to be meaningful — return empty set."""
    return _int_env("MEMORY_BLINDSPOT_LEARNED_STOPS_MIN_EPISODES", 30)


def reset_cache() -> None:
    """Test hook: clear the per-process cache."""
    with _STOPS_CACHE_LOCK:
        _STOPS_CACHE.clear()


def _cache_remember(key: tuple[str, str], value: frozenset[str]) -> None:
    """LRU eviction via dict insertion order. Lock-protected — see
    ``_BRIEF_CACHE`` for the same race rationale."""
    with _STOPS_CACHE_LOCK:
        _STOPS_CACHE.pop(key, None)
        _STOPS_CACHE[key] = value
        while len(_STOPS_CACHE) > _STOPS_CACHE_MAX:
            oldest = next(iter(_STOPS_CACHE))
            del _STOPS_CACHE[oldest]


def learn_workspace_stops(
    conn: sqlite3.Connection, *, workspace_id: str, lookback_days: int
) -> frozenset[str]:
    """Return tokens that appear in >= ``threshold`` fraction of
    recent episodes — the workspace's de facto common vocabulary.

    These get added on top of the project's static stopword list
    inside the V3 blindspot scanner.
    """
    if not is_enabled():
        return frozenset()
    cutoff = (datetime.now(UTC) - timedelta(days=lookback_days)).isoformat()
    cache_key = (workspace_id, cutoff[:10])  # day-level granularity
    with _STOPS_CACHE_LOCK:
        cached = _STOPS_CACHE.get(cache_key)
    if cached is not None:
        return cached
    excluded = excluded_source_types()
    placeholders = ",".join("?" * len(excluded)) if excluded else "''"
    try:
        rows = conn.execute(
            f"SELECT raw_text FROM episodes "
            f"WHERE workspace_id = ? AND created_at >= ? "
            f"AND source_type NOT IN ({placeholders})",
            (workspace_id, cutoff, *excluded),
        ).fetchall()
    except sqlite3.OperationalError:
        _cache_remember(cache_key, frozenset())
        return frozenset()
    if len(rows) < min_corpus_size():
        _cache_remember(cache_key, frozenset())
        return frozenset()
    n_episodes = len(rows)
    df: Counter[str] = Counter()
    for row in rows:
        text = str(row["raw_text"] or "") if isinstance(row, sqlite3.Row) else str(row[0] or "")
        # Use the v3.3 bigram tokenizer so the stopword set covers both
        # unigrams and bigrams — otherwise "compiled test" might slip
        # through even after we add "compiled" to the stops.
        for token in bigram_tokens(text):
            df[token] += 1
    cutoff_count = int(threshold() * n_episodes)
    stops = frozenset(tok for tok, count in df.items() if count >= cutoff_count)
    _cache_remember(cache_key, stops)
    return stops
