"""v3.5 sector-2 audit-followup: FTS query sanitizer + error-tolerance.

Three contracts locked:

1. Multi-token queries AND-join (not OR) — previously the OR-join
   collapsed BM25 precision and was the dominant factor in the
   NDCG@10=0.6694 plateau on BEIR SciFact.
2. Token count + total length caps prevent query-DoS via a 10 KB
   query expanding to thousands of postings scans.
3. Malformed FTS5 syntax raises ``sqlite3.OperationalError`` deep in
   ``conn.execute``; the function now catches + returns ``[]`` so the
   whole envelope route can't 500 (same fail-shape as the v3.4 enum
   drift incident).
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.fts.query import _MAX_FTS_TOKENS, _sanitize, search_chunks_fts


def test_multi_token_query_uses_and_not_or() -> None:
    """The v3.5 fix: tokens are space-joined (FTS5 implicit AND), not
    explicitly OR-joined. Lock the operator out of the output."""
    out = _sanitize("alpha beta gamma")
    assert " OR " not in out
    assert out == '"alpha" "beta" "gamma"'


def test_single_token_query_unchanged() -> None:
    """Single-token queries should be the same shape under both
    behaviours — the OR-join only mattered for >=2 tokens."""
    assert _sanitize("alpha") == '"alpha"'


def test_empty_query_returns_empty() -> None:
    assert _sanitize("") == ""
    assert _sanitize("   ") == ""
    # Only special chars → empty after sanitisation
    assert _sanitize("():*^+-") == ""


def test_token_count_capped() -> None:
    """v3.5 sector-2: more than ``_MAX_FTS_TOKENS`` tokens get
    truncated to prevent query-DoS via OR-blowup (now AND-blowup,
    same impact)."""
    many = " ".join(f"tok{i}" for i in range(_MAX_FTS_TOKENS + 50))
    out = _sanitize(many)
    # Count quoted phrases — should be exactly _MAX_FTS_TOKENS, not more
    assert out.count('"') == _MAX_FTS_TOKENS * 2


def test_oversized_query_truncated() -> None:
    """The byte cap kicks in even when the token count would be fine."""
    long_token = "a" * 5000
    out = _sanitize(long_token)
    # Cap is 256 chars on the cleaned string BEFORE quoting; output is
    # one quoted phrase, length at most _MAX_FTS_TOTAL_CHARS + quotes.
    assert len(out) < 5000


def test_malformed_fts_query_returns_empty_not_500(applied_conn: sqlite3.Connection) -> None:
    """v3.5 sector-2: any ``sqlite3.OperationalError`` from the FTS
    grammar (e.g. an FTS5 directive sneaking through future schema
    changes) must NOT escape — the function returns [] and the
    caller degrades gracefully. Hand-craft a query that DOES escape
    the sanitiser by forging the safe string after the fact via a
    monkeypatched _sanitize, since the current sanitiser is too
    thorough to produce a real FTS5 syntax error from any input."""
    import agent_memory_lite.fts.query as q  # noqa: PLC0415

    # Force the safe string to contain a structure FTS5 will reject.
    original = q._sanitize

    def _broken(_query: str) -> str:
        # Deliberately unmatched paren — FTS5 should raise OperationalError.
        return '"foo" "bar" )'

    q._sanitize = _broken  # type: ignore[assignment]
    try:
        result = search_chunks_fts(applied_conn, workspace_id="ws", query="anything")
    finally:
        q._sanitize = original  # type: ignore[assignment]
    assert result == []
