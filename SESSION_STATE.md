# SESSION_STATE.md — agent-memory-lite

Rolling state for cross-session work. Updated at every phase boundary. Pair-read with
`CLAUDE.md` (stable invariants).

## Current phase

**Phase 0 — Bootstrap (complete).** Ready to start Phase 1 (episodes + FTS + redaction).

## Last verified

`pip install -e ".[dev]"` succeeds on Python 3.14.3 (Windows). All gates green:

- `pytest` — 49 passed, 0 failed.
- `ruff check src tests scripts` — clean.
- `ruff format --check` — clean.
- `mypy src` (strict) — clean across 26 source files.
- `python scripts/bootstrap_db.py` — creates `./.agent_memory/memory.db` and applies
  both migrations.
- `python -m agent_memory_lite` — binds `127.0.0.1:8765`. `GET /health` returns the
  expected JSON; `POST /health` returns 405.
- Local-only guard rejects `https://api.openai.com` etc.; `OPENAI_API_KEY` in env is
  also rejected.

## Phase 0 deliverables landed

- Top-level: `pyproject.toml`, `.env.example`, `.gitignore`, `Makefile`, `run.bat`,
  `run.sh`, `README.md`, `CLAUDE.md`, `SESSION_STATE.md`.
- Migrations: `0001_init.sql` (12 tables + indexes), `0002_chunks_fts.sql` (FTS5),
  `migrations/README.md`.
- Source modules: `version.py`, `__init__.py`, `__main__.py`, `logging_setup.py`,
  `config/{settings,workspace_meta,local_only_guard}.py`,
  `db/{connection,migrations,transactions,pragmas}.py`, `models/enums.py`,
  `utils/{ids,time,hashing,pathing}.py`, `api/{app,deps,errors}.py`,
  `api/routes/health.py`, `scripts/bootstrap_db.py`.
- Tests: 49 cases under `tests/unit/{config,db,utils}/`, `tests/property/`,
  `tests/e2e/`. `conftest.py` provides `tmp_db_path`, `applied_conn`, `settings_factory`.

## Next phase — Phase 1: episodes + FTS + redaction + ST embeddings

Focus areas:
- `redaction/` subpackage (`patterns.py`, `secret_keywords.py`, `pii.py`, `redactor.py`)
  with property tests on idempotence and corpus coverage.
- `chunking/{text.py, line_ranges.py}` with property tests on reassembly +
  monotonic line numbers.
- `fts/{chunks_fts.py, query.py}` for FTS5 sync + bm25 ordered queries.
- `embeddings/{base.py, sentence_transformers_provider.py, ollama_provider.py,
  batching.py, dimension_check.py}`. ST is wired now; Ollama is a scaffold for
  Phase 3.
- Repositories: `episodes_repo.py`, `chunks_repo.py`, `audit_repo.py`.
- `ingestion/episode_pipeline.py` orchestrates redact → save → chunk → embed → FTS.
- `api/routes/{ingest_episode.py, search.py}` + matching `api/schemas/`.

Acceptance: `POST /memory/ingest_episode` writes a redacted episode + chunk + FTS
row; `POST /memory/search` (mode=fts) finds it by exact path/symbol/error string.

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
