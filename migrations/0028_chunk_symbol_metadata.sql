-- 1.4.0: indexable symbol metadata on chunks.
-- Pre-1.4.0 the only way to find "all chunks defining symbol X" was
-- LIKE '%X%' on symbols_json (slow, false-positive prone). The new
-- columns let /memory/find_symbols and /memory/explain_diff query
-- exact symbol kind + qualified name without scanning JSON.
--
-- All three columns are nullable so legacy chunks (CODE chunks
-- emitted before the tree-sitter dispatcher) and non-code chunks
-- (DOC, EPISODE, NOTE) keep their existing rows untouched.
--
-- symbol_kind:
--   'function' | 'class' | 'method' | 'struct' | 'interface' |
--   'enum' | 'type'
-- qualified_name:
--   'foo'                    bare top-level
--   'paperBot.calculate'     Python / JS / TS / Go / Rust / Java / C# method
--   'Foo::bar'               C++ method (different separator)
-- parent_qualified_name:
--   parent container's qualified_name (e.g. 'paperBot' for the method
--   above) or NULL for top-level symbols.
ALTER TABLE chunks ADD COLUMN symbol_kind TEXT;
ALTER TABLE chunks ADD COLUMN qualified_name TEXT;
ALTER TABLE chunks ADD COLUMN parent_qualified_name TEXT;

CREATE INDEX IF NOT EXISTS idx_chunks_qualified_name
    ON chunks (workspace_id, qualified_name)
    WHERE qualified_name IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_chunks_symbol_kind
    ON chunks (workspace_id, symbol_kind, qualified_name)
    WHERE symbol_kind IS NOT NULL;
