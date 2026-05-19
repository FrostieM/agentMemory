"""v3.1 Vector 4 — Learned causality via embedding similarity.

Builds on the existing ``causal_links`` table with a NEW relation
``semantically_similar_to``. The heuristic: for each pair of
chronologically-adjacent decisions in a workspace, if their text
embeddings have cosine similarity >= threshold, emit a directed
``causal_link(src=earlier, dst=later, relation=semantically_similar_to,
weight=cosine)``.

This is the "memory notices its own patterns" loop — without anyone
explicitly writing the connection, future decisions can be traced
back to their semantic ancestors via ``memory_recall`` (Phase 7
spreading activation already follows causal_links).

# Contract

* ``MEMORY_CAUSAL_EMBEDDING_ENABLED`` (default ``false``) — gates
  the whole module. When off, ``derive_workspace`` returns 0.
* Uses the live ``EmbeddingProvider`` from
  ``embeddings.factory.get_provider``. No new model dependency.
* Failure-soft: any error (missing provider, embedding crash,
  schema drift) returns 0 with no exception propagating.
* Idempotent: ``INSERT OR IGNORE`` via existing
  ``causal_extractor._upsert_causal_link`` — re-running is a no-op
  beyond a couple of SELECTs.

# Settings

* ``MEMORY_CAUSAL_EMBEDDING_ENABLED`` — default ``false``.
* ``MEMORY_CAUSAL_EMBEDDING_THRESHOLD`` — default ``0.75`` cosine.
* ``MEMORY_CAUSAL_EMBEDDING_WINDOW`` — default ``20`` decisions to
  scan (most recent first, by updated_at).
"""

from __future__ import annotations

import os
import sqlite3

from agent_memory_lite.retrieval.causal_extractor import _upsert_causal_link


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int, *, floor: int = 2) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return max(floor, int(raw))
    except ValueError:
        return default


def is_enabled() -> bool:
    return _bool_env("MEMORY_CAUSAL_EMBEDDING_ENABLED", False)


def similarity_threshold() -> float:
    return _float_env("MEMORY_CAUSAL_EMBEDDING_THRESHOLD", 0.75)


def window_size() -> int:
    return _int_env("MEMORY_CAUSAL_EMBEDDING_WINDOW", 20, floor=2)


def _decision_corpus(
    conn: sqlite3.Connection, workspace_id: str, limit: int
) -> list[tuple[str, str, str]]:
    """Return (id, text, updated_at) for the most-recent active decisions.

    Concatenates title + decision_text so the embedding captures
    "what was decided" rather than just the title.
    """
    try:
        rows = conn.execute(
            "SELECT id, COALESCE(title, '') || ' ' || COALESCE(decision_text, '') AS body, "
            "updated_at FROM decisions "
            "WHERE workspace_id = ? AND status = 'active' "
            "AND updated_at IS NOT NULL "
            "ORDER BY updated_at DESC LIMIT ?",
            (workspace_id, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[tuple[str, str, str]] = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            out.append((str(row["id"]), str(row["body"] or ""), str(row["updated_at"])))
        else:
            out.append((str(row[0]), str(row[1] or ""), str(row[2])))
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity for two equal-length lists.

    Assumes provider returns L2-normalized vectors (per the
    EmbeddingProvider contract), so cosine = dot product.
    """
    return sum(x * y for x, y in zip(a, b, strict=False))


def derive_workspace(conn: sqlite3.Connection, *, workspace_id: str) -> int:
    """Scan recent decisions for embedding-similar pairs and emit
    ``causal_link(relation=semantically_similar_to)`` rows. Returns
    the count of newly-inserted edges (idempotent — repeat calls add 0).
    """
    if not is_enabled():
        return 0
    rows = _decision_corpus(conn, workspace_id, window_size())
    if len(rows) < 2:
        return 0
    try:
        from agent_memory_lite.config.settings import get_settings  # noqa: PLC0415
        from agent_memory_lite.embeddings.factory import (  # noqa: PLC0415
            get_embedding_provider,
        )

        provider = get_embedding_provider(get_settings())
    except Exception:  # pragma: no cover - defensive against embeddings absence
        return 0
    bodies = [r[1] for r in rows]
    try:
        vectors = provider.embed_batch(bodies, kind="doc").tolist()
    except Exception:  # pragma: no cover - embedding crash → no-op
        return 0
    threshold = similarity_threshold()
    inserted = 0
    # Pair every later decision with every earlier one — O(N^2/2) but
    # bounded by window_size (20 default → 190 pairs).
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            cos = _cosine(vectors[i], vectors[j])
            if cos < threshold:
                continue
            # rows[0] is most recent. So earlier = rows[j], later = rows[i].
            ok = _upsert_causal_link(
                conn,
                workspace_id=workspace_id,
                src_kind="decision",
                src_id=rows[j][0],
                dst_kind="decision",
                dst_id=rows[i][0],
                relation="semantically_similar_to",
                weight=float(cos),
            )
            if ok:
                inserted += 1
    conn.commit()
    return inserted
