# SESSION_STATE.md - agent-memory-lite

Rolling state for cross-session work. Pair-read with `CLAUDE.md`.

## Current phase

**Research-lab, capability, and integrity hardening layers complete.** The
original v1 memory subsystem is feature-complete and now includes first-class
theory/research workflow objects plus roles, skills, playbooks, reviewable
memory candidates, retrieval-integrity audit/health, maintenance events, and
capability links. Context rendering is query-ranked so active execution and
research guidance stays visible before raw chunks. The theory layer stores
validation criteria, dependent decision links, evidence counts/strength, and
explicit `validated`/`rejected`/`superseded` lifecycle states. Capability links
let roles, skills, and playbooks directly influence theories and experiments
instead of staying as passive guidance.

## Last verified

All gates green on Python 3.14.3 (Windows):

- `pytest` - full suite passed.
- `ruff check src tests scripts` - clean.
- `ruff format --check src tests scripts` - clean.
- `mypy src` - clean across **167 source files**.
- `python scripts/run_evals.py --workspace <workspace_id> --no-vector` - 11/11 cases
  passed with recall@10=1.0 and precision@10=1.0.
- HTTP health on a project memory reported migrations `0001_init`,
  `0002_chunks_fts`, `0003_theories`, `0004_research_lab`,
  `0005_agent_capabilities`, `0006_theory_discipline`,
  `0007_memory_integrity_candidates`, and `0008_capability_research_links`.

## Research-lab and capability deliverables landed

Source:

- `migrations/0003_theories.sql` with `theories` and `theory_evidence`.
- `migrations/0004_research_lab.sql` with `memory_snapshots`,
  `research_experiments`, `experiment_results`, `domain_concepts`, and
  `research_insights`.
- `migrations/0005_agent_capabilities.sql` with `agent_roles`,
  `agent_skills`, and `agent_playbooks`.
- `migrations/0006_theory_discipline.sql` with theory validation criteria,
  dependent decision links, and evidence summary counters.
- `migrations/0007_memory_integrity_candidates.sql` with reviewable extraction
  candidates and persistent maintenance events.
- `migrations/0008_capability_research_links.sql` with role/skill/playbook
  links to theories, evidence, experiments, insights, candidates, and decisions.
- Theory models, repository, writer, routes, schemas, MCP registry tools, and
  `<active_theories>` context rendering.
- Research models, repository, writer, routes, schemas, MCP registry tools, and
  `<research_agenda>` context rendering.
- `memory_add_experiment_result` links experiment results to theory evidence,
  updates theory confidence/status, and creates contradiction insights for
  high-confidence refuting or mixed results.
- `memory_get_context` query-ranks and caps active decisions so current
  theories and research agenda stay visible as decision history grows.
- `scripts/research_status.py` reports theories, snapshots, open experiments,
  insights, concepts, and section visibility from a running service.
- `scripts/run_evals.py --no-vector` runs the eval harness without loading an
  embedding model or vector store.
- The real MCP stdio server exposes the expanded tool surface, not just the old
  base tools.
- Capability models, repository, writer, routes, schemas, MCP tools, and
  `<agent_capabilities>` context rendering.
- Capability-link models, repository, writer, routes, schemas, MCP tools, query
  ranking support, `<capability_links>` context rendering inside theories, and
  integrity audit checks for dangling capability links.
- `scripts/memory_audit.py` performs read-only retrieval-integrity audits and
  explicit backup-first repair/migration workflows.

Docs:

- `README.md` documents theory memory and the research-lab flow.
- `docs/AGENT_CONTRACT.md` describes snapshots, experiments, results, concepts,
  insights, roles, skills, playbooks, and workspace-id handling.
- `AGENT_SETUP/` prompts now tell agents to preserve research objects and to use
  the project's established workspace id instead of hard-coding a foreign one.
  The capture prompt also preserves reusable capability memory.

Tests:

- `tests/unit/ingestion/test_theory_writer.py`.
- `tests/e2e/test_theories_route.py`.
- `tests/unit/ingestion/test_research_writer.py`.
- `tests/e2e/test_research_routes.py`.
- `tests/unit/mcp/test_tools.py` now checks both the registry and stdio server
  tool exposure.
- `tests/unit/ingestion/test_capability_writer.py`.
- `tests/e2e/test_capabilities_routes.py`.
- `tests/e2e/test_capability_links_route.py`.
- `tests/unit/maintenance/test_integrity.py`.
- `tests/e2e/test_health.py` covers retrieval-integrity health summary.
- `tests/unit/evals/test_runner.py` covers `research_context` eval cases.
- `tests/e2e/test_get_context_route.py` covers decision capping/query ranking.

## v1 base scope

- Phase 0: bootstrap (config + migrations + FastAPI + local-only guard).
- Phase 1: episodes + FTS + redaction + sentence-transformers embeddings.
- Phase 2: vector store + hybrid retrieval + `/memory/get_context`.
- Phase 3: decisions + task state + core/procedural + extraction layer.
- Phase 4: lite temporal graph (entities, facts, conflict invalidation).
- Phase 5: file/project ingestion.
- Phase 6: compaction + eval harness + MCP base surface.
- Research layer: theories, snapshots, experiments, results, concepts, insights.
- Capability layer: roles, skills, playbooks.

## Locked-in decisions

- Embedding model: `intfloat/multilingual-e5-small` via sentence-transformers.
- LLM extraction: heuristic always on; Ollama (`qwen2.5:7b-instruct`) mandatory
  with a startup probe unless `OLLAMA_PROBE_SKIP=true`.
- Workspace ingest excludes: `.gitignore` + builtin denylist + optional
  `.memoryignore`.
- Project isolation is primarily by physical DB/vector paths. `workspace_id` is
  a logical namespace inside that database and must remain consistent with the
  project's established convention.

## How to resume

For a fresh session, read `SESSION_STATE.md` and `CLAUDE.md` in parallel. Then
call `memory_get_context` for the specific task before editing.
