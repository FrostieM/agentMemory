-- FTS5 virtual table for chunk-level exact search.
--
-- We do NOT install triggers here. The application layer (`fts/chunks_fts.py`) keeps
-- this table in sync with `chunks`. Reasons:
--   1. The `path` column comes from `files.path` (a join), which triggers cannot fetch
--      cleanly from a NEW row.
--   2. Application-managed sync is easier to test deterministically.
--   3. Failure modes are explicit (a missed sync surfaces as an empty FTS hit instead
--      of corrupt FTS state behind a trigger).
--
-- If we ever switch to triggers, add them in a follow-up migration; do NOT edit this one.

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    workspace_id UNINDEXED,
    path,
    symbols,
    text,
    summary,
    tokenize = 'unicode61'
);
