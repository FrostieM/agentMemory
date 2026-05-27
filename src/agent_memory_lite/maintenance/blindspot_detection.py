"""v3.1 Vector 3 — structural blindspot detection.

Surface tokens that get talked about a lot in ``episodes`` but never
become a ``decision``. The asymmetry is the signal: a topic that the
agent keeps mentioning without committing to an architectural choice
is either (a) unresolved tension worth a decision, (b) a recurring
problem that nobody's documented, or (c) operator vocabulary that
deserves a ``concept`` row. Cheapest first v3.1 capability per
``docs/V3_1_BREAKTHROUGH_ROADMAP.md``.

Read-only — never auto-creates rows. Surface via the brief and let the
agent (or operator at /ui/review) decide. Reuses the same token /
stopword pipeline as the capability + decision-neighbor suggesters so
"a token" means the same thing across the system.

Audit-round-2 hardening (2026-05-19):

* Opaque-id filter — tokens like ``ep_153e3ad``, ``dec_abc``,
  ``ins_xxx``, ``cm_yyy``, ``cand_zzz`` or pure-hex strings would
  otherwise dominate the list because every episode mentions row ids
  but no decision title uses them.
* Decision token-set cached per (workspace, max-updated-at) so the
  hot-path doesn't re-tokenize every active decision on each brief.

Settings:

* ``MEMORY_BLINDSPOT_DETECT_ENABLED`` — default ``true``.
* ``MEMORY_BLINDSPOT_LOOKBACK_DAYS`` — default 90.
* ``MEMORY_BLINDSPOT_MIN_EPISODES`` — default 5. Token must appear in
  at least this many DISTINCT episodes to count as a pattern.
* ``MEMORY_BLINDSPOT_LIMIT`` — default 5 rows per brief.
* ``MEMORY_BLINDSPOT_SAMPLE_TOKENS_PER_EPISODE`` — default 80. Caps the
  per-episode tokenization cost so a 1000-episode workspace finishes
  in milliseconds.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from agent_memory_lite.ingestion.capability_suggester import _tokenize
from agent_memory_lite.maintenance.blindspot_bigrams import (
    bigram_tokens as _bigram_tokens,
)
from agent_memory_lite.maintenance.blindspot_bigrams import (
    is_bigrams_enabled,
    is_compound_identifier,
)
from agent_memory_lite.maintenance.blindspot_decision_tokens import (
    decision_token_set as _decision_token_set,
)

# Re-exported so existing tests / callers that import the cache as
# ``blindspot_detection._DECISION_TOKENS_CACHE`` keep working after the
# SLOC-driven split into blindspot_filters.
from agent_memory_lite.maintenance.blindspot_filters import (
    DECISION_TOKENS_CACHE as _DECISION_TOKENS_CACHE,  # noqa: F401
)
from agent_memory_lite.maintenance.blindspot_filters import (
    emit_limit,
    excluded_source_types,
    is_enabled,
    lookback_days,
    min_episode_count,
    reset_decision_tokens_cache,
    sample_tokens_per_episode,
)
from agent_memory_lite.maintenance.blindspot_filters import (
    is_opaque_id as _is_opaque_id,
)
from agent_memory_lite.maintenance.blindspot_learned_stops import (
    learn_workspace_stops as _learn_workspace_stops,
)

# Re-export the env-helper functions so existing callers / tests that
# import them from this module keep working without churn.
__all__ = [
    "Blindspot",
    "emit_limit",
    "find_blindspots",
    "is_enabled",
    "lookback_days",
    "min_episode_count",
    "reset_decision_tokens_cache",
    "sample_tokens_per_episode",
]


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


def find_blindspots(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    days: int | None = None,
    min_episodes: int | None = None,
    limit: int | None = None,
    enrich: bool = True,
) -> list[Blindspot]:
    """Tokens appearing in ``>= min_episodes`` distinct episodes within
    the lookback window but in ZERO active decisions.

    Returns rows ordered by descending episode-count. Empty list when
    the feature is disabled, the workspace has no episodes, or no
    token clears the threshold.
    """
    if not is_enabled():
        return []
    horizon_days = days if days is not None else lookback_days()
    threshold = min_episodes if min_episodes is not None else min_episode_count()
    cap = limit if limit is not None else emit_limit()
    sample_cap = sample_tokens_per_episode()
    cutoff = (datetime.now(UTC) - timedelta(days=horizon_days)).isoformat()
    # Live-audit-2026-05-20: exclude auto-event source_types whose
    # episodes are mechanically-generated (e.g. ``file_indexed`` from
    # the pre-commit ingest hook) — they dominate the corpus and surface
    # directory / module / __init__ tokens that no architectural decision
    # would address. Override via ``MEMORY_BLINDSPOT_EXCLUDED_SOURCE_TYPES``
    # (comma-separated) when the deployment uses other auto-event types.
    excluded = excluded_source_types()
    placeholders = ",".join("?" * len(excluded)) if excluded else "''"
    try:
        ep_rows = conn.execute(
            f"SELECT raw_text FROM episodes WHERE workspace_id = ? AND created_at >= ? "
            f"AND source_type NOT IN ({placeholders})",
            (workspace_id, cutoff, *excluded),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    if not ep_rows:
        return []
    episode_counts: Counter[str] = Counter()
    for row in ep_rows:
        text = str(row["raw_text"] or "") if isinstance(row, sqlite3.Row) else str(row[0] or "")
        for token in _tokens_from(text, cap=sample_cap):
            if _is_opaque_id(token):
                continue
            episode_counts[token] += 1
    candidates = [tok for tok, n in episode_counts.items() if n >= threshold]
    if not candidates:
        return []
    decision_tokens = _decision_token_set(conn, workspace_id=workspace_id, days=horizon_days)
    # v3.3 last-mile: workspace-learned stopwords. Tokens that appear in
    # >=40% of episodes are workspace-common infrastructure ('compiled',
    # '27d') — not blindspots. Cached per (workspace, day) so repeated
    # brief renders skip the recomputation.
    learned_stops = _learn_workspace_stops(
        conn, workspace_id=workspace_id, lookback_days=horizon_days
    )
    out = [
        Blindspot(token=tok, episode_count=episode_counts[tok], decision_count=0)
        for tok in candidates
        if tok not in decision_tokens and tok not in learned_stops
    ]
    # v3.3: rank by (episode_count desc, compound-identifier first,
    # token-length desc). Compound identifiers (snake_case / CamelCase
    # / bigrams with at least one underscore) are domain-specific
    # concepts; they should beat plain unigrams at the same frequency.
    out.sort(
        key=lambda b: (
            -b.episode_count,
            0 if (is_compound_identifier(b.token) or " " in b.token) else 1,
            -len(b.token),
            b.token,  # final tiebreak: deterministic alpha order
        )
    )
    capped = out[:cap]
    if not enrich:
        return capped
    # v3.1 LLM augmentation — best-effort enrichment per row. Failure-soft.
    from agent_memory_lite.maintenance.blindspot_enrich import (  # noqa: PLC0415
        maybe_enrich_with_llm,
    )

    return maybe_enrich_with_llm(capped, ep_rows)
