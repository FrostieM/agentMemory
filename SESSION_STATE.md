# SESSION_STATE.md — agent-memory-lite

Rolling state for cross-session work. Updated at every phase boundary. Pair-read with
`CLAUDE.md` (stable invariants).

## Current phase

**Phase 5 — file / project ingestion (complete).** Ready to start Phase 6
(compaction + eval harness + MCP).

## Last verified

All gates green on Python 3.14.3 (Windows):

- `pytest` — **224 passed**, 0 failed.
- `ruff check src tests scripts` — clean.
- `ruff format --check` — clean.
- `mypy src` (strict) — clean across **122 source files**.
- `POST /memory/ingest_file` writes file row + chunks + FTS rows + audit, with
  idempotent re-ingest by `content_hash`.
- `python scripts/ingest_workspace.py --path <repo>` walks the workspace,
  applies excludes, and ingests file-by-file.

## Phase 5 deliverables landed

Source:
- Models + repo for `files` (path, language, content_hash, last_indexed_at).
- `chunking/symbols.py` — Python via `ast`, fallback regex for other languages.
- `chunking/code.py` — chunks Python by top-level FunctionDef/ClassDef nodes;
  falls back to the plain-text packer with regex-derived symbols when AST
  parsing fails or the language isn't Python.
- `chunking/markdown.py` — split by `#` heading; oversized sections fall
  through to the plain-text packer while keeping the heading attribution.
- `ingestion/exclude_rules.py` — fnmatch-style matcher for builtin denylist
  (`.git/`, `node_modules/`, `__pycache__/`, `.venv/`, `dist/`, `build/`,
  `*.min.js`, `*.lock`, `*.lance/`) plus `.gitignore` and `.memoryignore`.
  Negation patterns (`!pattern`) are honoured.
- `ingestion/workspace_scanner.py` — walks a workspace, applies the patterns,
  drops binary files (`\x00` heuristic) and files larger than 1 MB.
- `ingestion/file_pipeline.py` — idempotent file ingest. On unchanged content
  it returns `skipped=True` with no new rows; on changed content it deletes
  the prior chunks + FTS rows for the file and writes new ones. Records each
  ingest as a `FILE_INDEXED` episode + audit entry.
- `api/schemas/ingest.py` extended with `IngestFileRequest` + response.
- `api/routes/ingest_file.py` — `POST /memory/ingest_file`.
- `scripts/ingest_workspace.py` — CLI driver.

Tests added (24 new cases, 224 total):
- `tests/unit/chunking/{test_code, test_markdown, test_symbols}.py` — covers
  Python AST split, regex fallback, markdown heading split, oversized section
  fallback, and symbol extraction in mixed languages.
- `tests/unit/ingestion/test_exclude_rules.py` — builtin denylist, gitignore
  loading, `.memoryignore` overlay (including negation), extra patterns.
- `tests/integration/test_file_pipeline_e2e.py` — first ingest creates chunks,
  re-ingest of unchanged content skips, re-ingest of changed content drops
  the old chunks and writes the new ones, markdown is chunked by heading.

## Next phase — Phase 6: compaction + eval harness + MCP

Focus areas:
- `compaction/{summarize_old, invalidate_stale, promote_durable}.py` and
  `compaction/__init__.memory_compact` — summarize old episodes, invalidate
  stale facts (closed `valid_to` long ago), promote durable+trusted candidates
  to core memory.
- `api/routes/compact.py` (`POST /memory/compact`).
- `evals/{runner, metrics, reporters}.py` + YAML fixtures
  (`retrieval_basic.yaml`, `conflict_resolution.yaml`, `trust_gating.yaml`).
- `api/schemas/evals.py` + `api/routes/evals.py` (`POST /memory/run_evals`).
- `scripts/run_evals.py`.
- `mcp/{server, tools}.py` — stdio MCP server reusing the service functions.
- Acceptance: eval JSON report shows recall@10, precision@10, stale_fact_rate,
  secret_leak_count, prompt_injection_failures; MCP stdio server boots and
  exposes the tool list.

## Locked-in decisions

- Embedding model: `intfloat/multilingual-e5-small` via sentence-transformers.
- LLM extraction: heuristic always on; Ollama (`qwen2.5:7b-instruct`)
  **mandatory** with a startup probe (skippable via `OLLAMA_PROBE_SKIP=true`
  for CI/tests).
- Workspace ingest excludes: `.gitignore` + builtin denylist + optional
  `.memoryignore`.
- v1 single-workspace, hard-coded `workspace_id="default"`.

## Open questions

None right now.

## How to resume

Read this file and `CLAUDE.md` in parallel. Pick up at the Phase 6 focus list
above. Existing fixtures (`applied_conn`, `fake_embedding_provider`,
`fake_vector_store`, `app_factory`) remain available.
