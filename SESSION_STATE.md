# SESSION_STATE.md — agent-memory-lite

Rolling state for cross-session work. Updated at every phase boundary. Pair-read with
`CLAUDE.md` (stable invariants).

## Current phase

**Phase 4 — lite temporal graph (complete).** Ready to start Phase 5
(file / project ingestion).

## Last verified

All gates green on Python 3.14.3 (Windows):

- `pytest` — **200 passed**, 0 failed.
- `ruff check src tests scripts` — clean.
- `ruff format --check` — clean.
- `mypy src` (strict) — clean across **113 source files**.
- The graph layer surfaces facts as `<retrieved_facts>` inside the existing
  `/memory/get_context` envelope (alongside the chunk results).

## Phase 4 deliverables landed

Source:
- Models + repos for `entities` and `facts` (with `valid_from` / `valid_to` /
  `invalidated_by_fact_id`).
- `graph/canonicalize.canonicalize_name` — lowercases, collapses whitespace,
  strips outer punctuation.
- `graph/upsert_entity.upsert_entity` — find-or-create on
  `(workspace_id, type, canonical_name)`; merges aliases + properties on hit.
- `graph/conflict_detector.find_conflicting_facts` — open facts with the same
  `(subject_entity_id, relation)`. New writes always supersede.
- `graph/invalidate.invalidate_facts` — closes prior facts in batch by stamping
  `valid_to` and `invalidated_by_fact_id`.
- `graph/write_fact.write_fact` — atomic insert + conflict invalidation +
  audit row inside a single SQLite transaction.
- `graph/traversal.traverse_facts` — bounded BFS (defaults: max_hops=2,
  max_facts=40). Default search hides invalidated facts; `historical=True`
  surfaces the full timeline.
- `retrieval/candidates_graph.collect_graph` — match query tokens against
  canonical names + aliases, traverse, return `RetrievalCandidate` rows.
- `retrieval/context_builder._render_facts` — emits the `<retrieved_facts>`
  block with `relation`, `confidence`, `valid_from`, `valid_to` attrs.

Tests added (18 new cases, 200 total):
- `tests/unit/graph/{test_canonicalize, test_upsert_entity, test_write_fact,
  test_traversal}.py`.
- `tests/property/test_conflict_detection.py` — for any number of conflicting
  writes, exactly one fact stays open and every closed fact's invalidation
  chain reaches the open one without a cycle (hypothesis).
- `tests/property/test_traversal_bounds.py` — bounded BFS respects
  `max_hops` and `max_facts` for all chain lengths (hypothesis).

## Next phase — Phase 5: file / project ingestion

Focus areas:
- Domain model + repo for `files`.
- `chunking/{code,markdown,symbols}.py` — code chunking by AST when possible
  (Python via `ast`, others via regex), markdown by heading.
- `ingestion/{file_pipeline,workspace_scanner,exclude_rules}.py`.
- `.gitignore` parser + builtin denylist + optional `.memoryignore` overlay.
- `api/schemas/ingest.py` extension + `api/routes/ingest_file.py`.
- `scripts/ingest_workspace.py` — CLI to crawl a workspace.
- Idempotent re-ingest: hash-based dedupe, drop chunks for changed files.
- Add `<retrieved_chunks>` path attribute population from `files.path`.

Acceptance: `python scripts/ingest_workspace.py --workspace default --path
<repo>` ingests respecting `.gitignore` + builtin denylist + `.memoryignore`;
re-running on unchanged files inserts no new chunks; FTS finds files by path,
function name, and error text; vector search finds semantic matches.

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

Read this file and `CLAUDE.md` in parallel. Pick up at the Phase 5 focus list
above. Existing fixtures (`applied_conn`, `fake_embedding_provider`,
`fake_vector_store`, `app_factory`) remain available.
