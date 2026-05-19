"""Opaque-id filter + decision-token-set cache + env helpers for blindspots.

Extracted from ``blindspot_detection`` to keep that module under the
150-SLOC ceiling after the audit-round-2 hardening (opaque-id filter +
caching). Three concerns lived here naturally:

* ``is_opaque_id`` — reject row-id mentions / hex blobs / numerics
  that would otherwise dominate the blindspot list because every
  episode references row ids but no decision title uses them.
* ``DECISION_TOKENS_CACHE`` — bounded LRU on (workspace_id,
  max_updated_at) so repeated brief renders inside one mutation
  interval don't re-tokenize every active decision.
* Env-flag readers — ``is_enabled``, ``lookback_days``,
  ``min_episode_count``, ``emit_limit``, ``sample_tokens_per_episode``.
"""

from __future__ import annotations

import os
import re

OPAQUE_ID_PREFIXES: tuple[str, ...] = (
    "ep_",
    "dec_",
    "ins_",
    "cm_",
    "cand_",
    "rid_",
    "skl_",
    "rol_",
    "ply_",
    "chk_",
    "th_",
)
# Tokenizer always lowercases (capability_suggester._tokenize) so the
# hex/digit regexes only need to match lowercase + arabic digits.
_PURE_HEX_RE = re.compile(r"^[0-9a-f]{6,}$")
# Audit-round-3 tightening: previous ``\d`` matched ANY digit, dropping
# legitimate tech tokens (``python3``, ``sha1``, ``oauth2``, ``utf8``,
# ``cl100k``). 4+ consecutive digits is a reliable signal for ids /
# timestamps / hex chunks without false-positive on tech vocabulary.
_RUN_OF_DIGITS_RE = re.compile(r"\d{4,}")


def is_opaque_id(token: str) -> bool:
    """``True`` for row-id mentions, hex blobs, or digit-run tokens.

    What survives the filter:
      * Pure-alpha tech terms with a trailing digit or two — ``python3``,
        ``sha1``, ``oauth2``, ``utf8``, ``cl100k``, ``v3``, ``b2b``.
    What is filtered:
      * Row-id prefixes (`ep_/dec_/ins_/cm_/cand_/rid_/skl_/rol_/ply_/chk_/th_`)
      * Pure-hex strings ≥6 chars (sha-fragments)
      * Any token with ≥4 consecutive digits (timestamps, ids, years
        like ``2026``, port numbers, byte offsets).
    """
    if token.startswith(OPAQUE_ID_PREFIXES):
        return True
    if _PURE_HEX_RE.match(token):
        return True
    return bool(_RUN_OF_DIGITS_RE.search(token))


DECISION_TOKENS_CACHE: dict[tuple[str, str], set[str]] = {}
DECISION_TOKENS_CACHE_MAX = 8


def reset_decision_tokens_cache() -> None:
    """Test hook: clear the per-process decision-token-set cache."""
    DECISION_TOKENS_CACHE.clear()


def cache_remember(key: tuple[str, str], tokens: set[str]) -> None:
    """LRU-ish insert with bounded eviction.

    Audit-round-3 fix: pop the incoming key FIRST so a refreshed entry
    is treated as most-recent (otherwise on a full cache the eviction
    step could remove the very key we're about to write, since
    ``next(iter(...))`` picks the oldest by insertion order).
    """
    DECISION_TOKENS_CACHE.pop(key, None)
    while len(DECISION_TOKENS_CACHE) >= DECISION_TOKENS_CACHE_MAX:
        oldest = next(iter(DECISION_TOKENS_CACHE))
        del DECISION_TOKENS_CACHE[oldest]
    DECISION_TOKENS_CACHE[key] = tokens


# Env-flag readers (kept here so blindspot_detection stays under 150 SLOC).


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, floor: int = 1) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(floor, value)


def is_enabled() -> bool:
    return _bool_env("MEMORY_BLINDSPOT_DETECT_ENABLED", True)


def lookback_days() -> int:
    return _int_env("MEMORY_BLINDSPOT_LOOKBACK_DAYS", 90, floor=1)


def min_episode_count() -> int:
    return _int_env("MEMORY_BLINDSPOT_MIN_EPISODES", 5, floor=2)


def emit_limit() -> int:
    return _int_env("MEMORY_BLINDSPOT_LIMIT", 5, floor=1)


def sample_tokens_per_episode() -> int:
    return _int_env("MEMORY_BLINDSPOT_SAMPLE_TOKENS_PER_EPISODE", 80, floor=10)


# Live-audit-2026-05-20 finding: pre-commit ``file_indexed`` events
# dominate the corpus on workspaces with active code-memory ingestion.
# Their token surface (directory / module names) creates 100%
# false-positive blindspots. Defaults skip every auto-event source_type
# we ship; operator can override with a comma-separated env list.
_DEFAULT_EXCLUDED_SOURCE_TYPES: tuple[str, ...] = (
    "file_indexed",
    "file_ingest",
    "code_chunk_indexed",
    "auto_ingest",
)


def excluded_source_types() -> tuple[str, ...]:
    """Auto-event source_types skipped by the blindspot scanner.

    Returns the env override when present, else the shipped defaults.
    """
    raw = os.environ.get("MEMORY_BLINDSPOT_EXCLUDED_SOURCE_TYPES", "").strip()
    if not raw:
        return _DEFAULT_EXCLUDED_SOURCE_TYPES
    parts = tuple(item.strip() for item in raw.split(",") if item.strip())
    return parts or _DEFAULT_EXCLUDED_SOURCE_TYPES
