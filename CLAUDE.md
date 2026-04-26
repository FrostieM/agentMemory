# CLAUDE.md — agent-memory-lite

Stable invariants and conventions for any agent working on this repo. Pair-read with
`SESSION_STATE.md` (current phase, last decision, next file). At session start, read both
in parallel before responding.

## What this project is

Local memory subsystem for an AI agent: SQLite (WAL, FTS5) as source-of-record, LanceDB
for embedded vector search, sentence-transformers for embeddings, Ollama for LLM-driven
candidate extraction. FastAPI on `127.0.0.1:8765`. Single-workspace v1 (`workspace_id="default"`),
schema is multi-workspace-ready.

## Hard rules

- **Local-only**. No cloud LLM, embedding, or vector providers. Ever. The startup guard
  rejects non-loopback URLs and cloud hostnames; ruff bans cloud SDK imports.
- **No Docker**. No Postgres / pgvector / Neo4j / Graphiti / Redis. Upgrade path is
  documented in the spec but not in scope for this repo.
- **Source files ≤ 150 SLOC**, one concern per module. The spec's mono-files become
  subpackages: `retrieval/`, `graph/`, `extraction/`, `chunking/`, `redaction/`,
  `vector_store/`, `embeddings/`, `ingestion/`, `compaction/`, `api/routes/`.
- **Paired tests**. Every non-trivial source module has a paired unit test. Business
  logic uses `hypothesis` for property-based testing (redaction, chunking, RRF, scoring,
  conflict detection, traversal bounds, migration idempotence).
- **English in repo**. Code, comments, commit messages, markdown — English. Russian
  only in the chat with the user.
- **Forward-only migrations**. `migrations/NNNN_*.sql`, tracked by `schema_migrations`
  table. No down migrations. Escape hatch: delete `memory.db`.
- **Secrets never stored**. `redaction/` runs before any text reaches SQLite or LanceDB.
- **Untrusted documents** stay untrusted. `extraction/trust_gate.py` blocks promotion of
  document-sourced candidates to core memory or procedural rules.

## Layered architecture

```
api/routes/      ← thin: schema validation, calls service
ingestion/       ← orchestrates a write pipeline (redact → save → embed → fts → extract → graph)
retrieval/       ← orchestrates the read pipeline (normalize → fts+vector+graph → fuse → score → filter → context)
extraction/      ← heuristic + LLM extractors, threshold gates, trust gate
graph/           ← entities + facts ops with valid_from/valid_to and supersedes
compaction/      ← summarize old, invalidate stale, promote durable
repositories/    ← thin SQL wrappers, one per table; no business logic
db/              ← connection, migrations, transactions, pragmas
config/          ← settings, workspace_meta, local_only_guard
models/          ← pydantic domain types
api/schemas/     ← pydantic wire types (separate from domain types)
utils/           ← ids, time, hashing, pathing, tokens
```

A module never imports across the boundary "down to up" (e.g. `db/` does not import
`api/`). Repositories are the only layer that runs SQL. Services compose repositories.
Routes call services.

## Shared contracts (lock these early)

- `EmbeddingProvider` (Protocol) — `name`, `dim`, `embed_batch(texts, kind)`.
- `VectorStore` (Protocol) — `upsert`, `query`, `delete`, `drop_namespace`, `count`.
- `Extractor` (Protocol) — `extract(episode) → list[MemoryCandidate]`.
- `MemoryCandidate` (pydantic) — `kind`, `payload`, `confidence`, `trust_level`, `source_episode_id`.
- `RetrievalHit` / `ScoredHit` (pydantic) — uniform shape across FTS / vector / graph paths.
- `RedactedText` (pydantic) — `text`, `spans`, `kinds_seen`.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate    # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
python scripts/bootstrap_db.py
ollama pull qwen2.5:7b-instruct                       # mandatory
python -m agent_memory_lite                           # binds to 127.0.0.1:8765
```

Quality gates before merging anything:

```bash
pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
mypy src
```

## Phase boundaries

Phase 0 → 6 are defined in the plan. At every phase boundary, update `SESSION_STATE.md`
with: current phase, last decision, the exact next file to create, and any open question.

## When in doubt

- New feature without a paired test? Stop and add the test.
- Module growing past 150 SLOC? Split into a subpackage before adding more.
- A URL going somewhere new? Update `local_only_guard.CLOUD_DENYLIST` and add a guard test.
- An LLM call failing? Surface a clear error pointing to Ollama install — never silently
  fall back to a no-op for the mandatory extractor.
