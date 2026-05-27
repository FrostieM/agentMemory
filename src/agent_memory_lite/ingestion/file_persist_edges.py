"""1.5.0: hard-graph edge persistence for the file ingest pipeline.

After ``persist_chunk`` writes every code chunk, this helper:

1. Builds the ``qualified_name → chunk_id`` map from the freshly
   inserted chunks (so an edge owner ``Beta.gamma`` resolves to the
   chunk we just wrote).
2. Calls the language-specific edge extractor (Python: stdlib AST;
   other languages: skipped in Phase 2 — tree-sitter edges land
   later).
3. Resolves each edge's ``src_chunk_id`` from the map (edges from
   ``<module>`` attach to the first chunk in the file so module-
   level imports are reachable from at least one chunk).
4. Resolves ``dst_chunk_id`` for within-file targets; leaves NULL
   for external imports / stdlib / not-yet-seen symbols (a future
   resolver pass populates them as the workspace fills out).
5. Inserts every edge in bulk.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.extraction.symbol_edges_python import (
    ExtractedEdge,
    extract_python_edges,
)
from agent_memory_lite.extraction.symbol_edges_ts import extract_ts_edges
from agent_memory_lite.models.symbol_edges import EdgeIn
from agent_memory_lite.repositories.symbol_edges_repo import insert_edges
from agent_memory_lite.repositories.symbol_edges_resolver import (
    resolve_pending_edges_for_qnames,
)

# Languages handled by the tree-sitter edge extractor (1.5.2).
_TS_EDGE_LANGS: frozenset[str] = frozenset(
    {"javascript", "typescript", "go", "rust", "java", "cpp", "csharp"}
)
_MAX_QNAME_LENGTH = 400
_MAX_EDGE_TYPE_LENGTH = 32


def _build_owner_index(chunk_qnames: list[tuple[str, str | None]]) -> dict[str, str]:
    """``[(chunk_id, qualified_name), ...]`` → ``{qualified_name: chunk_id}``."""
    out: dict[str, str] = {}
    for chunk_id, qname in chunk_qnames:
        if qname:
            out.setdefault(qname, chunk_id)
    return out


def _extract_edges(text: str, language: str | None) -> list[ExtractedEdge]:
    lang = (language or "").lower()
    if lang == "python":
        return extract_python_edges(text)
    if lang in _TS_EDGE_LANGS:
        # 1.5.2: tree-sitter walker for the other 7 supported languages.
        # Falls back to [] when the grammar package isn't installed —
        # the service stays usable without C extensions.
        return extract_ts_edges(text, lang)
    return []


def _valid_edge(edge: ExtractedEdge) -> bool:
    return (
        0 < len(edge.src_qualified_name) <= _MAX_QNAME_LENGTH
        and 0 < len(edge.dst_qualified_name) <= _MAX_QNAME_LENGTH
        and 0 < len(edge.edge_type) <= _MAX_EDGE_TYPE_LENGTH
    )


def persist_edges_for_file(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    text: str,
    language: str | None,
    chunk_qnames: list[tuple[str, str | None]],
) -> int:
    """Extract + persist edges for one file. Returns count written.

    1.5.1 also runs the cross-file resolver pass so any pre-existing
    NULL-dst edges that target this file's symbols are now linked.
    """
    extracted = _extract_edges(text, language)
    owner_index = _build_owner_index(chunk_qnames)

    # 1.5.1: resolver pass — even when this file's body has no edges
    # of its own, the chunks it just wrote may be the targets of
    # earlier files' pending edges. Run the resolver every time so
    # cross-file dependencies stitch together as the workspace fills.
    if owner_index:
        resolve_pending_edges_for_qnames(
            conn,
            workspace_id=workspace_id,
            qname_to_chunk_id=owner_index,
        )

    if not extracted or not owner_index:
        return 0
    # Module-level edges attach to the first chunk in the file so they
    # are reachable from at least one node. Without this, ``import x``
    # edges would have no src_chunk_id and the schema requires NOT NULL.
    fallback_chunk_id = chunk_qnames[0][0] if chunk_qnames else None
    edges_in: list[EdgeIn] = []
    for edge in extracted:
        if not _valid_edge(edge):
            continue
        src_chunk_id = owner_index.get(edge.src_qualified_name)
        if src_chunk_id is None:
            if edge.src_qualified_name != "<module>" or fallback_chunk_id is None:
                continue
            src_chunk_id = fallback_chunk_id
        dst_chunk_id = owner_index.get(edge.dst_qualified_name)
        edges_in.append(
            EdgeIn(
                workspace_id=workspace_id,
                src_chunk_id=src_chunk_id,
                src_qualified_name=edge.src_qualified_name,
                dst_qualified_name=edge.dst_qualified_name,
                dst_chunk_id=dst_chunk_id,
                edge_type=edge.edge_type,
                src_language=language,
            )
        )
    return insert_edges(conn, edges_in)
