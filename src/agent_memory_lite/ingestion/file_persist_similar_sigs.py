"""2.1.4: similar_signature soft-edge accumulator.

After every newly-versioned chunk, compare its signature against
the last ``scan_limit`` distinct signatures from
``symbol_versions`` in the same workspace. When the estimated
Jaccard ≥ ``threshold``, emit a ``similar_signature`` soft edge
in both directions (mirroring the v1.7 ``co_changed`` symmetry).

Bounded scan keeps cost predictable on large workspaces. A future
LSH-indexed variant could replace this for sub-linear lookup, but
the bounded brute-force pass is fine up to a few thousand symbols.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.extraction.signature_similarity import (
    is_empty,
    jaccard,
    minhash,
)
from agent_memory_lite.repositories.soft_edges_repo import upsert_soft_edge


def _candidate_signatures(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    exclude_qname: str,
    scan_limit: int,
) -> list[tuple[str, str]]:
    """Return the most recent (qualified_name, signature_text) pairs
    in this workspace, deduped by qname (latest signature wins),
    excluding ``exclude_qname``. Used as the candidate pool for
    Jaccard comparison.
    """
    rows = conn.execute(
        "SELECT qualified_name, signature_text FROM symbol_versions "
        "WHERE workspace_id = ? AND qualified_name <> ? "
        "ORDER BY created_at DESC LIMIT ?",
        (workspace_id, exclude_qname, scan_limit),
    ).fetchall()
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for r in rows:
        qn = str(r["qualified_name"])
        sig = str(r["signature_text"] or "")
        if qn in seen or not sig.strip():
            continue
        seen.add(qn)
        out.append((qn, sig))
    return out


def record_similar_signature_edges(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    new_qname: str,
    new_signature: str,
    settings: Settings | None,
) -> int:
    """Compare ``new_signature`` against recent prior signatures and
    emit ``similar_signature`` soft edges (both directions) for
    pairs over the threshold. Returns count of edges written.
    Returns 0 when disabled or signature is too short to tokenize.
    """
    if settings is None or not settings.similar_sig_enabled:
        return 0
    new_mh = minhash(new_signature)
    if is_empty(new_mh):
        return 0
    candidates = _candidate_signatures(
        conn,
        workspace_id=workspace_id,
        exclude_qname=new_qname,
        scan_limit=settings.similar_sig_scan_limit,
    )
    written = 0
    for other_qname, other_sig in candidates:
        other_mh = minhash(other_sig)
        if is_empty(other_mh):
            continue
        score = jaccard(new_mh, other_mh)
        if score < settings.similar_sig_threshold:
            continue
        upsert_soft_edge(
            conn,
            workspace_id=workspace_id,
            src=new_qname,
            dst=other_qname,
            kind="similar_signature",
            weight_increment=score,
        )
        upsert_soft_edge(
            conn,
            workspace_id=workspace_id,
            src=other_qname,
            dst=new_qname,
            kind="similar_signature",
            weight_increment=score,
        )
        written += 2
    return written
