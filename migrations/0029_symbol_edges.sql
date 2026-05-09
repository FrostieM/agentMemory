-- 1.5.0 Phase 2: hard graph edges between symbol chunks.
-- Pre-1.5.0 the file ingest pipeline stored chunks as isolated nodes;
-- there was no record that ``paperBot.calculate`` calls
-- ``Selector.admit`` or that ``CircleCI`` extends ``IShape``. This
-- table captures those edges so an agent can ask "who references X?"
-- without scanning every chunk body for a substring.
--
-- Edge model:
--   src_chunk_id           the chunk where the edge is written
--                          (e.g. the function body that contains the
--                          call site)
--   src_qualified_name     denormalized for fast lookup without a join
--   dst_qualified_name     edge target's qualified name; may be a
--                          symbol that doesn't have a chunk yet
--                          (external import, stdlib symbol, future
--                          method) — those are still recorded so a
--                          later resolver pass can populate dst_chunk_id
--   dst_chunk_id           nullable; resolved when an in-workspace
--                          chunk matches dst_qualified_name
--   edge_type              'calls' | 'imports' | 'exports' |
--                          'extends' | 'implements' | 'references' |
--                          'instantiates' | 'decorated_by'
--   src_language           pinned at write time so cross-language
--                          edges are filterable
--
-- Edges are not soft-deletable: when a file is re-ingested, the
-- ingest pipeline drops every edge with src_chunk_id pointing at the
-- old chunks (the chunks themselves are dropped first). Edges with
-- only dst_chunk_id pointing at dropped chunks survive (re-resolves
-- on next read).
CREATE TABLE IF NOT EXISTS symbol_edges (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    src_chunk_id TEXT NOT NULL,
    src_qualified_name TEXT NOT NULL,
    dst_qualified_name TEXT NOT NULL,
    dst_chunk_id TEXT,
    edge_type TEXT NOT NULL,
    src_language TEXT,
    created_at TEXT NOT NULL,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_symbol_edges_src
    ON symbol_edges (workspace_id, src_chunk_id);

CREATE INDEX IF NOT EXISTS idx_symbol_edges_dst_qname
    ON symbol_edges (workspace_id, dst_qualified_name, edge_type);

CREATE INDEX IF NOT EXISTS idx_symbol_edges_dst_chunk
    ON symbol_edges (workspace_id, dst_chunk_id)
    WHERE dst_chunk_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_symbol_edges_src_qname
    ON symbol_edges (workspace_id, src_qualified_name, edge_type);
