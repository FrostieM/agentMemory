# SESSION_STATE.md — agent-memory-lite

Rolling state for cross-session work. Updated at every phase boundary. Pair-read with
`CLAUDE.md` (stable invariants).

## Current phase

**Phase 6 — compaction + eval harness + MCP (complete).** All six phases of
the plan have landed. The lite memory subsystem is feature-complete for v1.

## Last verified

All gates green on Python 3.14.3 (Windows):

- `pytest` — **246 passed**, 0 failed.
- `ruff check src tests scripts` — clean.
- `ruff format --check` — clean.
- `mypy src` (strict) — clean across **136 source files**.
- `POST /memory/compact`, `POST /memory/run_evals`, and the matching
  `scripts/run_evals.py` script all run end-to-end.
- The MCP tool registry (`agent_memory_lite.mcp.tools.TOOLS` + `dispatch`)
  exposes `memory_get_context`, `memory_ingest_episode`, `memory_ingest_file`,
  `memory_write_decision`, `memory_update_task_state`.

## Phase 6 deliverables landed

Source:
- `compaction/{summarize_old, invalidate_stale, promote_durable}.py` plus the
  `compaction/__init__.py` re-exports. `summarize_old_episodes` writes one
  `EpisodeSource.SUMMARY` episode per period; original episodes stay intact.
  `archive_stale_facts` tags long-closed facts with `metadata.stale=True`
  without deleting (so historical mode still surfaces them).
  `promote_durable_candidates` runs trust-gated, threshold-passing candidates
  into `core_memory`.
- `api/schemas/compact.py` + `api/routes/compact.py` (`POST /memory/compact`).
- `evals/{runner, metrics, reporters}.py` with shared `EvalReport` + recall@K
  + precision@K helpers.
- `evals/fixtures/{retrieval_basic, redaction_basic, trust_gating,
  prompt_injection}.yaml` — first set of canonical eval cases.
- `api/schemas/evals.py` + `api/routes/evals.py` (`POST /memory/run_evals`).
- `scripts/run_evals.py` — CLI driver with both console and JSON output.
- `mcp/{server, tools}.py` — tool registry + dispatcher reusing the same
  service functions used by the HTTP routes.
- `pyproject.toml` declares `PyYAML` as a dep and ships the eval YAML fixtures
  via `[tool.setuptools.package-data]`.

Tests added (22 new cases, 246 total):
- `tests/unit/compaction/{test_summarize_old, test_promote_durable}.py`.
- `tests/integration/test_compaction_e2e.py` — end-to-end summarization +
  stale-fact archival against tmp DBs (uses time-provider override).
- `tests/unit/evals/{test_metrics, test_runner}.py` — recall/precision math
  and YAML-driven runner across all four case types.
- `tests/unit/mcp/test_tools.py` — tool registry shape + unknown-tool dispatch.

## v1 scope (all phases)

- Phase 0: bootstrap (config + migrations + FastAPI + local-only guard).
- Phase 1: episodes + FTS + redaction + ST embeddings.
- Phase 2: vector store + hybrid retrieval + `/memory/get_context`.
- Phase 3: decisions + task state + core/procedural + extraction layer.
- Phase 4: lite temporal graph (entities, facts, conflict invalidation).
- Phase 5: file / project ingestion (idempotent re-ingest, exclude rules).
- Phase 6: compaction + eval harness + MCP tool surface.

## Out of scope (would land in v2)

- Multi-workspace API surface (the column is in every table; only the API
  hard-codes `default`).
- Postgres / pgvector / Neo4j / Graphiti / Redis upgrade path.
- Cloud LLM/embedding/vector providers — explicitly forbidden by the
  local-only guard.
- A real MCP stdio JSON-RPC transport. The tool registry + dispatcher are
  ready; users plug in any MCP runtime they prefer.
- Tree-sitter symbol extraction beyond Python (`ast`) and the regex fallback.

## Locked-in decisions

- Embedding model: `intfloat/multilingual-e5-small` via sentence-transformers.
- LLM extraction: heuristic always on; Ollama (`qwen2.5:7b-instruct`)
  **mandatory** with a startup probe (skippable via `OLLAMA_PROBE_SKIP=true`
  for CI/tests).
- Workspace ingest excludes: `.gitignore` + builtin denylist + optional
  `.memoryignore`.
- v1 single-workspace, hard-coded `workspace_id="default"`.

## How to resume

The plan file at `C:\Users\Osino\.claude\plans\async-moseying-sutton.md`
remains the canonical implementation reference. For a fresh session, read
this `SESSION_STATE.md` and `CLAUDE.md` in parallel; everything else is
discoverable from the code and the eval suite.
