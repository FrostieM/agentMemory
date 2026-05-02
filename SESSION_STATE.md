# SESSION_STATE.md — agent-memory-lite

Rolling state for cross-session work. Pair-read with `CLAUDE.md`.

## Current state — 1.0.0 stable

agent-memory-lite has shipped its first stable release. The full memory model
(18+ kinds), retrieval pipeline (RRF FTS + vector + graph), operator surface
(pin / archive / what_references / list_audit / state snapshots / review queue
/ compact watchdog), hub mode + asymmetric isolation, and the live browser
observatory are all in main and covered by the test matrix.

## Quality gates

All green on Python 3.13 / 3.14 (Windows / macOS / Linux):

- `pytest -q` — **485 tests** (unit / property / integration / e2e).
- `ruff check src tests scripts` — clean.
- `ruff format --check src tests scripts` — clean.
- `mypy src` — strict, clean across 394 source files.
- `python scripts/check_sloc.py --enforce` — every `src/**/*.py` ≤ 150 SLOC.
- `python scripts/run_evals.py --workspace <ws> --no-vector` — eval cases pass
  with the recall / precision / stale-fact / leak targets from the spec.
- `python scripts/crash_test_v3.py` against a fresh `qa-crash` workspace —
  70/70 assertions verifying retrieval, search, context envelope,
  cross-references, pin/archive semantics, capability links, audit trail,
  hygiene queue, snapshots, and relationship integrity end-to-end.

## What you get out of the box

Persistence kinds covered by `migrations/0001_init.sql` (consolidated v1.0.0
schema):

- **Logging:** `episodes`, `chunks`, `files`, `audit_log`, `maintenance_events`.
- **Decisions / theories / research:** `decisions`, `theories`,
  `research_experiments`, `experiment_results`, `memory_snapshots`,
  `research_insights`, `domain_concepts`.
- **Capabilities:** `agent_roles`, `agent_skills`, `agent_playbooks`,
  `capability_links`.
- **Behavior:** `behavior_instructions`, `core_memory`, `task_state`,
  `procedural_rules`.
- **Graph:** `entities`, `facts`.
- **Review / triage:** `memory_candidates`, `memory_usage_feedback`.
- **Memory observability:** `memory_state_snapshots`, `vector_index_metadata`.
- **Workspace bookkeeping:** `workspace_manifest`, `workspace_meta`,
  `schema_migrations`.
- **FTS5 virtual table:** `chunks_fts` (synced application-side).

Operator endpoints (HTTP + MCP, identical surface):

- **Read:** `get_context`, `explain_context`, `search`, `get_object`,
  `list_decisions / theories / candidates / behavior_instructions /
  research_agenda / agent_capabilities / capability_links /
  maintenance_events / audit`, `what_references`, `snapshot_list`,
  `snapshot_diff`, `review_queue`, `hygiene_report`, `quality_gate`,
  `workspaces`, `health`.
- **Write:** `ingest_episode`, `ingest_file`, `write_decision`,
  `write_theory`, `add_theory_evidence`, `register_snapshot`,
  `write_experiment`, `add_experiment_result`, `upsert_concept`,
  `distill_insight`, `update_insight`, `upsert_agent_role / skill /
  playbook`, `link_capability`, `upsert_behavior_instruction`,
  `update_task_state`, `promote_candidate / reject_candidate`,
  `resolve_maintenance_event`, `archive`, `pin`, `snapshot_save`,
  `compact_trigger`, `record_usage_feedback`, `compact`, `run_evals`.

Memory-quality features (env-flagged, off by default):

- **Episode dedup** — `MEMORY_EPISODE_DEDUP_ENABLED=1` plus
  `MEMORY_EPISODE_DEDUP_THRESHOLD` (default 0.92) and
  `MEMORY_EPISODE_DEDUP_WINDOW` (default 50).
- **Confidence decay** — `MEMORY_CONFIDENCE_DECAY_ENABLED=1` plus
  `MEMORY_CONFIDENCE_DECAY_HALF_LIFE_DAYS` (default 14).
- **Auto conflict detection** — `MEMORY_CONFLICT_DETECT_ENABLED=1` plus
  `MEMORY_CONFLICT_DETECT_THRESHOLD` (default 0.6).
- **Token-aware compaction watchdog** — `MEMORY_COMPACT_TRIGGER_THRESHOLD_CHUNKS`
  (default 0 = disabled).

## Locked-in decisions

- Embedding model: `intfloat/multilingual-e5-small` via sentence-transformers
  (CPU, 384-dim vectors).
- LLM extraction: heuristic extractor always on; Ollama (`qwen2.5:7b-instruct`)
  mandatory with a startup probe unless `OLLAMA_PROBE_SKIP=true`.
- Vector store: LanceDB default (per-workspace namespace); `sqlite-vec` is
  opt-in.
- Workspace ingest excludes: `.gitignore` + builtin denylist + optional
  `.memoryignore`.
- Project isolation by physical DB / vector paths; `workspace_id` is the
  logical namespace inside that DB and must stay consistent with the
  project's established convention.
- Forward-only migrations. `migrations/0001_init.sql` is the consolidated
  v1.0.0 schema. Subsequent post-1.0 migrations chain on top normally.

## Hub mode + asymmetric isolation

A single local HTTP service on `127.0.0.1:8765` serves many per-project
SQLite + LanceDB pairs through a workspace registry at
`~/.agent_memory/workspaces.json`. The MCP stdio server is registry-aware:
every tool call resolves the right physical DB from `workspace_id` via
per-call `X-Memory-DB-Path` headers. Default for project chats is
**asymmetric isolation** — reads to any registered workspace are allowed,
writes to a foreign workspace are blocked at the strict-isolation guard.
Hub chats opened in a parent dir (or with `MEMORY_HUB_MODE=true`) opt out
of strict isolation for cross-project maintenance.

The `inject_memory_context.py` UserPromptSubmit hook auto-bootstraps a
shared "global" workspace under `~/.agent_memory/global/` when the cwd
has no registered workspace, so a chat opened anywhere still gets memory
context. Override / opt-out via `AGENT_MEMORY_HOOK_FALLBACK=disabled`,
`AGENT_MEMORY_FALLBACK_WORKSPACE`, `AGENT_MEMORY_FALLBACK_DIR`.

## Observability — `/ui`

The browser UI at `http://127.0.0.1:8765/ui` renders a live graph of memory
operations as they happen: family bubbles for each kind, spokes for in-flight
objects, the current request stage, a recent-events trail, and a workspace
dropdown to switch between registered projects. Inspector cards expose
`Pin / Archive` flips for decisions / behavior_instructions / core_memory.
Animation auto-coalesces same-intent same-family bursts, so 12 simultaneous
`WRITE_DECISION` events render as one cycle showing all 12 spokes instead
of 12 sequential cycles.

## How to resume

For a fresh session: read `CLAUDE.md` and `AGENTS.md` in parallel. Then call
`memory_get_context` for the specific task before editing. Pin the
operator-critical invariants (`local-only`, `forbid_cloud_egress`, etc.) so
they always appear in the active context envelope.
