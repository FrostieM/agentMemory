"""1.5.1: cross-file edge resolver for the hard graph.

Split from ``symbol_edges_repo.py`` to keep the repo focused on
basic CRUD. When a new file is ingested, every edge in this
workspace whose ``dst_chunk_id IS NULL`` AND ``dst_qualified_name``
matches one of the new qnames gets its ``dst_chunk_id`` populated.

This closes the cross-file import case: file A imports ``B.foo``
before file B is ingested, so the edge starts with
``dst_chunk_id=NULL``; once B is ingested and ``B.foo`` lands as a
chunk, this resolver rewrites the edge so ``graph_neighbors`` can
navigate from A → B.foo.
"""

from __future__ import annotations

import sqlite3


def resolve_pending_edges_for_qnames(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    qname_to_chunk_id: dict[str, str],
) -> int:
    """Match each ``(qname, chunk_id)`` pair against pending NULL
    ``dst_chunk_id`` edges and update them. Returns count resolved.

    Two match patterns per qname:
    1. exact: ``dst_qualified_name = qname``  (``import foo`` →
       chunk ``foo``)
    2. suffix: ``dst_qualified_name LIKE '%.qname'``  (``from x import
       foo`` wrote ``x.foo`` while the local chunk's qualified_name
       is the bare ``foo``)
    """
    if not qname_to_chunk_id:
        return 0
    total = 0
    for qname, chunk_id in qname_to_chunk_id.items():
        cur = conn.execute(
            "UPDATE symbol_edges SET dst_chunk_id = ? "
            "WHERE workspace_id = ? "
            "AND dst_chunk_id IS NULL "
            "AND (dst_qualified_name = ? OR dst_qualified_name LIKE ?)",
            (chunk_id, workspace_id, qname, f"%.{qname}"),
        )
        total += int(cur.rowcount)
    return total
