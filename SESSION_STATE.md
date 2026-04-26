# SESSION_STATE.md — agent-memory-lite

Rolling state for cross-session work. Updated at every phase boundary. Pair-read with
`CLAUDE.md` (stable invariants).

## Current phase

**Phase 2 — vector search + hybrid retrieval (complete).** Ready to start
Phase 3 (decisions, task state, core memory, procedural rules, full context).

## Last verified

All gates green on Python 3.14.3 (Windows):

- `pytest` — **147 passed**, 0 failed.
- `ruff check src tests scripts` — clean.
- `ruff format --check` — clean.
- `mypy src` (strict) — clean across **79 source files**.
- e2e: `POST /memory/ingest_episode` writes redacted episode + chunk + FTS row +
  vector upsert; `POST /memory/get_context` returns an XML envelope with
  retrieved chunks pulled from FTS + vector via RRF; `POST /memory/search`
  (FTS mode) still works; `GET /health` unaffected.

## Phase 2 deliverables landed

Source:
- `vector_store/{base,lancedb_store,sqlite_vec_store,namespaces,factory,reindex}.py`.
  - `VectorStore` Protocol; LanceDB is default; `sqlite-vec` opt-in with graceful
    fallback. `reindex_chunks` rebuilds `chunks` namespace from SQLite.
- `retrieval/{normalize,candidates_fts,candidates_vector,fusion_rrf,scoring,
  filters,token_budget,context_builder}.py`.
  - Pipeline: normalize → FTS + vector candidates → RRF fusion → score (weights
    from spec, semantic + keyword + RRF presence boost wired; graph/recency/
    importance/confidence default neutral until Phases 3 & 4) → filter
    (pass-through in Phase 2) → token budget → XML envelope.
- `models/retrieval.py` — `RetrievalQuery`, `RetrievalCandidate`, `ScoredHit`.
- `api/schemas/context.py` + `api/routes/context.py` for `POST /memory/get_context`.
- `api/deps.py` extended with `EmbeddingProviderDep` and `VectorStoreDep`
  singletons + reset hook for tests.
- `ingestion/episode_pipeline.py` now calls `pin_or_check`, embeds the chunk,
  and upserts into the `chunks` vector namespace after the SQLite commit;
  vector failures log + leave the chunk durable for `scripts/reindex_vectors.py`.
- `scripts/reindex_vectors.py` — CLI to rebuild LanceDB from SQLite.

Tests added (37 new cases, 147 total):
- `tests/unit/retrieval/{test_normalize,test_fusion_rrf,test_scoring,
  test_filters,test_token_budget}.py`.
- `tests/property/{test_rrf_correctness,test_scoring_monotonicity}.py` (hypothesis).
- `tests/unit/vector_store/{test_namespaces,test_fake_store_contract}.py`.
- `tests/integration/test_retrieval_pipeline_e2e.py` — full hybrid pipeline +
  workspace isolation + FTS-only fallback.
- `tests/e2e/test_get_context_route.py`.
- `FakeVectorStore` + `app_factory` fixture added to `conftest.py` so e2e tests
  override the embedding provider and vector store with deterministic in-memory
  fakes (no model download, no LanceDB on-disk dependency).

## Next phase — Phase 3: decisions, task state, core memory, procedural rules

Focus areas:
- Domain models + repositories: `decisions.py`, `core_memory.py`, `task_state.py`,
  `procedural.py` and matching `repositories/*`.
- Ingestion writers: `decision_writer.py` (with supersedes-chain handling and
  audit), `task_state_writer.py`, `core_memory_writer.py`, `procedural_writer.py`.
- Extraction: `extraction/{base, heuristic_extractor, llm_extractor, thresholds,
  trust_gate}.py`. **Wire the mandatory Ollama probe at startup** (planned for
  this phase per locked-in decisions). Heuristic extractor always on; LLM
  extractor hits Ollama if reachable.
- Extend `retrieval/context_builder.py` to emit `<core_memory>`, `<task_state>`,
  `<active_decisions>`, and `<procedural_rules>` sections in priority order.
- New API routes: `POST /memory/write_decision`, `POST /memory/update_task_state`,
  matching `api/schemas/`.
- Snapshot test on the rendered XML envelope (`syrupy`).

Acceptance: writing a decision with `supersedes_id` closes the prior decision
(sets `valid_to`, `status='superseded'`). `get_context` includes core, task,
decisions, rules. `trust_gate` blocks promotion of `untrusted_doc` candidates
to core or procedural.

## Locked-in decisions

- Embedding model: `intfloat/multilingual-e5-small` via sentence-transformers.
- LLM extraction: heuristic always on; Ollama (`qwen2.5:7b-instruct`) **mandatory**
  for LLM-driven extraction in Phase 3 (startup probe will fail loudly if
  unreachable). Auto-promotion to core/procedural is gated by `trust_gate`
  regardless of extractor.
- Workspace ingest excludes: `.gitignore` + builtin denylist + optional `.memoryignore`.
- v1 single-workspace, hard-coded `workspace_id="default"`.

## Open questions

None right now.

## How to resume

Read this file and `CLAUDE.md` in parallel. Pick up at the Phase 3 focus list
above. Existing test fixtures (`applied_conn`, `fake_embedding_provider`,
`fake_vector_store`, `app_factory`) cover the new code's needs.
