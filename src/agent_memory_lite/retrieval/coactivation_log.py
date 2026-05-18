"""Phase 2: staging-side of the Hebbian co-retrieval loop.

When ``storage/reader.search`` returns a hit set, it calls
``log_coactivation`` to append one row per hit into
``retrieval_coactivation``. A sentinel-scheduled pass
(``maintenance/hebbian_pass.py``) reads the table, distills co-occurrence
into weighted ``soft_edges``, then prunes rows older than the configured
retention horizon.

This module is **failure-soft** by design: a single search must never
fail because of a Hebbian write hiccup. Every public call wraps the
SQL in ``try/except sqlite3.Error`` and returns silently on error.

Tokens / privacy: ``query_hash`` is a SHA-1 of the normalized query
string. The raw query is never persisted -- only the hash + the
returned (kind, id, rank). This keeps the table to a few-dozen-byte
row footprint and dodges any redaction concerns.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterable
from typing import Any

from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

# Whitespace-collapse + lowercase normalization. Keep it cheap: this runs
# on every search call. We intentionally do NOT stem / strip stopwords;
# different agents asking literally the same question should end up in
# the same Hebbian bucket regardless of token-frequency tweaks.
_WS_RE = re.compile(r"\s+")


def _normalize_query(query: str) -> str:
    return _WS_RE.sub(" ", query.strip().lower())


def query_hash(query: str) -> str:
    """Stable hash of a query string. Same input → same 16-char digest."""
    norm = _normalize_query(query)
    if not norm:
        return ""
    return hashlib.sha1(norm.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def log_coactivation(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str,
    hits: Iterable[Any],
    max_hits: int = 10,
) -> int:
    """Insert one ``retrieval_coactivation`` row per hit. Returns row count.

    ``hits`` is an iterable of either ``SearchHit`` dataclasses (with
    ``.kind`` + ``.projection['id']``) or plain dicts with ``kind`` /
    ``id``. Caller passes search hits in rank order; ``rank`` is the
    1-based position in that order. The duck-type accepts both so the
    function works for the reader hot path and direct test harnesses.

    Failure-soft: pre-migration DBs (no ``retrieval_coactivation`` table)
    silently no-op. SQL errors are swallowed so a search never raises
    because of telemetry.
    """
    qh = query_hash(query)
    if not qh:
        return 0
    rows: list[tuple[str, str, str, str, str, int, str]] = []
    now = iso_now()
    for rank, hit in enumerate(hits, start=1):
        if rank > max_hits:
            break
        kind, item_id = _extract_kind_id(hit)
        if not kind or not item_id:
            continue
        rows.append((new_id(IdKind.AUDIT), workspace_id, qh, kind, item_id, rank, now))
    if not rows:
        return 0
    try:
        conn.executemany(
            "INSERT INTO retrieval_coactivation "
            "(id, workspace_id, query_hash, item_kind, item_id, rank, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    except sqlite3.Error:
        return 0
    return len(rows)


def _extract_kind_id(hit: Any) -> tuple[str, str]:
    """Pull (kind, id) out of either a SearchHit dataclass or a dict."""
    if hasattr(hit, "kind") and hasattr(hit, "projection"):
        kind = str(getattr(hit, "kind", "") or "")
        proj = getattr(hit, "projection", {}) or {}
        item_id = str(proj.get("id") or proj.get("file_path") or "")
        return kind, item_id
    if isinstance(hit, dict):
        kind = str(hit.get("kind") or "")
        item_id = str(hit.get("id") or hit.get("file_path") or "")
        return kind, item_id
    return "", ""


def prune_log(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    retention_days: int,
    now_iso: str | None = None,
) -> int:
    """Drop coactivation rows older than ``retention_days``.

    Returns the number of rows deleted. Called by the Hebbian pass after
    distillation; safe to call independently for ad-hoc cleanup.
    """
    if retention_days <= 0:
        return 0
    cutoff_iso = _cutoff(now_iso or iso_now(), retention_days)
    try:
        cur = conn.execute(
            "DELETE FROM retrieval_coactivation WHERE workspace_id = ? AND created_at < ?",
            (workspace_id, cutoff_iso),
        )
        conn.commit()
    except sqlite3.Error:
        return 0
    return cur.rowcount or 0


def _cutoff(now_iso: str, retention_days: int) -> str:
    from datetime import timedelta  # noqa: PLC0415

    from agent_memory_lite.utils.time import parse_iso  # noqa: PLC0415

    try:
        return (parse_iso(now_iso) - timedelta(days=retention_days)).isoformat()
    except (TypeError, ValueError):
        return ""
