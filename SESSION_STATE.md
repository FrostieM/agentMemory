# SESSION_STATE.md — agent-memory-lite

Rolling state for cross-session work. Updated at every phase boundary. Pair-read with
`CLAUDE.md` (stable invariants).

## Current phase

**Phase 1 — episodes + FTS + redaction + embeddings (complete).** Ready to start
Phase 2 (vector search + hybrid retrieval).

## Last verified

All gates green on Python 3.14.3 (Windows):

- `pytest` — **110 passed**, 0 failed.
- `ruff check src tests scripts` — clean.
- `ruff format --check` — clean.
- `mypy src` (strict) — clean across **60 source files**.
- `python -m agent_memory_lite` boots; `POST /memory/ingest_episode` redacts +
  persists episode + chunk + FTS row; `POST /memory/search` (mode=fts) finds
  it by token; `GET /health` reports both migrations applied.

## Phase 1 deliverables landed

Source:
- Models: `episodes.py`, `chunks.py`, `audit.py`, `candidates.py`.
- Repositories: `episodes_repo.py`, `chunks_repo.py`, `audit_repo.py`.
- `redaction/{patterns,secret_keywords,pii,redactor}.py` with the public
  `redact(text)` returning `RedactedText`.
- `chunking/{line_ranges,text}.py` with paragraph-by-token packing.
- `fts/{chunks_fts,query}.py` — application-managed FTS5 sync + BM25 query.
- `embeddings/{base,batching,dimension_check,factory,
  sentence_transformers_provider,ollama_provider}.py` (ST default, Ollama opt-in).
- `ingestion/episode_pipeline.py` — redact → save episode → save chunk → FTS row → audit.
- `api/schemas/{ingest,search}.py` + `api/routes/{ingest_episode,search}.py`.
- `utils/tokens.py` — chars/4 token estimator with override hook.

Tests added (61 new cases, 110 total):
- `tests/unit/redaction/test_redactor.py` — 14 cases.
- `tests/property/test_redaction_invariants.py` — idempotence + secret coverage
  (hypothesis).
- `tests/unit/chunking/{test_line_ranges,test_text}.py` — 14 cases.
- `tests/property/test_chunking_invariants.py` — non-overlap, in-order, lossless
  re-assembly (hypothesis).
- `tests/unit/embeddings/{test_base_contract,test_batching,test_dimension_check}.py`.
- `tests/unit/fts/test_chunks_fts.py` — 7 cases.
- `tests/integration/test_episode_pipeline_e2e.py` — 4 cases including rollback.
- `tests/e2e/test_ingest_routes.py` — 4 cases over HTTP.
- `FakeEmbeddingProvider` added to `conftest.py` (hashes input → fixed-dim
  normalized vector; no model download in tests).

## Next phase — Phase 2: vector search + hybrid retrieval

Focus areas:
- `vector_store/{base,lancedb_store,sqlite_vec_store,namespaces,reindex}.py` —
  LanceDB default, sqlite-vec opt-in, factory falls back gracefully.
- `retrieval/{normalize,candidates_fts,candidates_vector,fusion_rrf,scoring,
  filters,token_budget,context_builder}.py` — first end-to-end retrieval pass.
- `models/retrieval.py`.
- `api/schemas/context.py`, `api/routes/context.py` for `POST /memory/get_context`.
- `scripts/reindex_vectors.py`.
- Wire `embedding_provider` into the episode pipeline so vectors land in LanceDB
  alongside the chunk insert.

Acceptance: `POST /memory/get_context` returns an XML-like block within the token
budget mixing FTS and vector hits with sources/confidence; `scripts/reindex_vectors.py`
rebuilds LanceDB from `chunks` table.

## Locked-in decisions

- Embedding model: `intfloat/multilingual-e5-small` via sentence-transformers.
- LLM extraction: heuristic always on; Ollama (`qwen2.5:7b-instruct`) **mandatory** —
  startup fails if Ollama unreachable. (Phase 0 ships a placeholder that does NOT
  yet probe Ollama; the probe lands in Phase 3 when extraction is wired.)
- Workspace ingest excludes: `.gitignore` + builtin denylist + optional `.memoryignore`.
- v1 single-workspace, hard-coded `workspace_id="default"`.

## Open questions

None right now.

## How to resume

Read this file and `CLAUDE.md` in parallel. Pick up at the Phase 1 focus list above.
