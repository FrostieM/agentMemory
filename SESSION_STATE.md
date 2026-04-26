# SESSION_STATE.md — agent-memory-lite

Rolling state for cross-session work. Updated at every phase boundary. Pair-read with
`CLAUDE.md` (stable invariants).

## Current phase

**Phase 3 — decisions, task state, core memory, procedural rules + extraction
scaffold (complete).** Ready to start Phase 4 (lite temporal graph).

## Last verified

All gates green on Python 3.14.3 (Windows):

- `pytest` — **182 passed**, 0 failed.
- `ruff check src tests scripts` — clean.
- `ruff format --check` — clean.
- `mypy src` (strict) — clean across **101 source files**.
- e2e: `POST /memory/write_decision` (with `supersedes_id` chain),
  `POST /memory/update_task_state`, `POST /memory/get_context` (now emits
  `<core_memory>`, `<task_state>`, `<active_decisions>`, `<procedural_rules>`,
  `<retrieved_chunks>`), `POST /memory/ingest_episode`, `POST /memory/search`.
- Ollama probe runs at startup unless `OLLAMA_PROBE_SKIP=true` (set in tests).

## Phase 3 deliverables landed

Source:
- Models + repos for `decisions`, `task_state`, `core_memory`, `procedural_rules`.
  Each repo is thin SQL only; the writers handle business rules.
- Ingestion writers (in `ingestion/`):
  - `decision_writer.write_decision` — supersedes-chain handling: closes prior
    `valid_to`, flips `status='superseded'`, atomic with the new insert.
    Cross-workspace supersedes rejected (`ValidationError`); unknown supersedes
    id → `NotFoundError` (404).
  - `task_state_writer.write_task_state` — upsert keyed by
    `(workspace_id, task_id)`; before/after state captured in audit.
  - `core_memory_writer.write_core_memory` — deactivate prior active row for
    the same `key`, insert new active row.
  - `procedural_writer.{write_procedural_rule, archive_procedural_rule}`.
- Extraction layer:
  - `extraction/base.py` — `Extractor` Protocol.
  - `heuristic_extractor.py` — regex cues for `Decision:` / `Решение:` /
    `Rule:` / `Правило:` lines. Always available.
  - `llm_extractor.py` — Ollama-backed; structured JSON prompt; failures log
    and return `[]` rather than raising into the ingest path.
  - `probe_ollama(settings)` — startup-time reachability check; raises
    `ExtractorUnavailableError` unless `OLLAMA_PROBE_SKIP=true`.
  - `thresholds.py` — per-kind confidence + importance minima from spec.
  - `trust_gate.py` — `untrusted_doc` cannot become a constraint or procedural
    rule; promotable kinds also require `user_asserted` / `verified_by_tool`
    / `explicit_decision` trust.
- `retrieval/context_builder.py` extended to render the four new sections.
  `historical=True` surfaces all decisions including superseded ones.
- API: `api/schemas/{decisions,task_state}.py` + `api/routes/{decisions,
  task_state}.py` for `POST /memory/write_decision` and
  `POST /memory/update_task_state`.
- `api/app.py` runs `probe_ollama` after migrations.

Tests added (35 new cases, 182 total):
- `tests/unit/ingestion/{test_decision_writer, test_task_state_writer,
  test_core_memory_writer, test_procedural_writer}.py`.
- `tests/unit/extraction/{test_thresholds, test_trust_gate,
  test_heuristic_extractor}.py`.
- `tests/e2e/{test_decisions_route, test_task_state_route}.py` — including
  decision + task state visibility through `POST /memory/get_context`.

## Next phase — Phase 4: lite temporal graph

Focus areas:
- Models + repos: `entities.py`, `facts.py`.
- `graph/{upsert_entity, write_fact, conflict_detector, invalidate, traversal,
  canonicalize}.py`.
- Wire entity upsert + fact write into `ingestion/episode_pipeline.py` after
  candidate extraction passes the trust gate + thresholds.
- `retrieval/candidates_graph.py` for graph hits; extend `context_builder.py`
  with `<retrieved_facts>`.
- Property tests: conflict detection (no cycles, exactly one open fact per
  subject+predicate), traversal bounds (≤ 40 facts, ≤ 2 hops, deterministic).
- Integration test: two contradicting episodes → exactly one open fact in the
  default view, both visible in historical mode.

Acceptance: graph hits flow through retrieval; default search hides invalidated
facts; historical mode surfaces them with `valid_to` and `invalidated_by_fact_id`.

## Locked-in decisions

- Embedding model: `intfloat/multilingual-e5-small` via sentence-transformers.
- LLM extraction: heuristic always on; Ollama (`qwen2.5:7b-instruct`) **mandatory**
  with a startup probe (skippable via `OLLAMA_PROBE_SKIP=true` for CI/tests).
- Workspace ingest excludes: `.gitignore` + builtin denylist + optional `.memoryignore`.
- v1 single-workspace, hard-coded `workspace_id="default"`.

## Open questions

None right now.

## How to resume

Read this file and `CLAUDE.md` in parallel. Pick up at the Phase 4 focus list
above. Existing fixtures (`applied_conn`, `fake_embedding_provider`,
`fake_vector_store`, `app_factory`) cover the new code's needs.
