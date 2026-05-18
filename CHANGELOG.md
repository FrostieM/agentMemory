# Changelog

All notable changes to **agent-memory-lite**. This file starts at the
2.0.0 baseline; the incremental 1.x → 2.1.x → 2.2.x development
history that produced 2.0.0 is preserved verbatim in
[`docs/CHANGELOG_LEGACY.md`](docs/CHANGELOG_LEGACY.md).

Versioning follows semver from 2.0.0 onward. Minor bumps add
functionality (and may flip a default), patch bumps fix bugs without
behavioural change.

## 3.0.0-dev — Unreleased (v3 development branch)

**Architectural pivot.** The audit findings in [`docs/POST_V2_ROADMAP.md`](docs/POST_V2_ROADMAP.md)
showed three binding constraints: token cost (every tool call returned
500-2000 tokens of full markdown body), tool-surface bloat (30+ MCP
tools, 71 env vars, 32 SQL migrations), and the adoption gap (~80%
rule-adherence plateau). v3 keeps SQL as source of truth and adds a
compact-projection retrieval layer; tool surface collapses to 9 MCP
tools + 2 hook primitives, 14 migrations consolidate into one DDL.

Target metrics on cutover: tokens/Claude Code session ≤50% of v2,
rule adherence ≥95%, brief cache hit rate ≥70%, code-digest staleness
median ≤2h. Acceptance gate runs via `scripts/v3_acceptance_gate.py`.

The 3.0.0-dev work runs in parallel with the stable 2.x surface; both
ship from the same checkout. v3 endpoints mount at `/v3/memory/*`,
v3 MCP tools are prefixed `memory_*`, v2 surface keeps working
unchanged until the week-8 canary flip.

### Foundation (Phase 1)

- **`migrations/v3/0001_init.sql`** — consolidated DDL replacing
  v2 migrations 0001 + 0020-0032. 35 tables: 12 core kinds
  (decisions, theories, behaviors, skills, concepts, tasks,
  episodes, insights, code_digests, chunks, theory_evidence,
  versions) + audit_log + FTS5 virtual tables + indexes.
  `gist` / `*_one_line` / `*_short` columns persisted on write
  so projections are pre-computed.
- **`scripts/migrate_v2_to_v3.py`** — idempotent SQLite-to-SQLite
  port. Resumable on partial failure. Computes gist columns via
  cheap heuristic during the port; Ollama backfill is a separate
  optional pass.
- **Compact projections** (`src/agent_memory_lite/v3/storage/projections.py`)
  — per-kind ~20-40-token shapes. Decision projection: `{id, title,
  status, gist, supersedes, valid_from}`. Full content opt-in via
  `memory_get(kind, id, fields=[...])`.
- **Reader + writer** with versioned mutations. Every write
  snapshots prior content to `versions` table; `memory_rollback`
  writes historical content as a new version (linear history).

### Cognition (Phase 2)

- **`memory_brief`** — ≤500-token session-start brief composed
  from 5 sections (identity 100 + behaviors 120 + decisions 130 +
  state 60 + code_hubs 90). Replaces v2's verbose `<memory_context>`
  envelope (~1500 tokens). In-process LRU cache keyed on workspace
  fingerprint; cache hit ~sub-millisecond, miss ~30-80ms (pure SQL,
  no LLM).
- **`memory_lint`** — pre-task advisory wrapping the existing
  `enforcement/dispatch.py` mechanical + semantic stack. Returns
  `{verdict, applicable_rules, related_decisions, prior_failures,
  watch_outs}`. Failure-soft: any error degrades to `allow`.
- **Auto file-digest pipeline** — `scripts/post_edit_enqueue.py`
  PostToolUse hook drops markers into `~/.agent_memory/digest_queue.jsonl`;
  `agent_memory_lite.v3.cognition.digest_worker` daemon consumes
  queue, runs heuristic per-file digest (Python AST top symbols +
  docstring first line; generic prefix scan for non-Python), UPSERTs
  into `code_digests` table. Hard cap 5000 pending tasks.
- **Sleep-time consolidation cron** —
  `agent_memory_lite.v3.cognition.consolidation` runs every 6h
  (03/09/15/21 local via `scripts/memory_consolidation_task.ps1`):
  clusters last-24h episodes by Jaccard similarity (0.30 threshold),
  distills one insight candidate per cluster. Catches up after sleep
  via `-StartWhenAvailable`.
- **Optional cross-encoder reranker** — opt-in `[rerank]` extra
  installs `sentence-transformers + torch`. `memory_search`
  accepts `rerank=true` and reorders the top-N hits by
  `jina-reranker-v1-tiny-en` (~33 MB CPU). Failure-soft: extra not
  installed or model load fails → falls back to BM25 order silently.

### Agent surface (Phase 1+2)

- **HTTP `/v3/memory/*`** — 13 endpoints returning uniform
  `{ok, data, error}` envelope. Routes are thin wrappers around v3
  storage / cognition.
- **9 MCP tools** (`memory_*` prefix during transition):
  6 strict (search, get, write, edit, pin, archive) + 2 hook
  primitives (brief, lint) + invoke_skill. Registered alongside
  v2 tools without collisions.
- **`memory-cli`** entrypoint — 13 subcommands mirroring HTTP.
  For shell agents (Aider, Codex CLI, CI scripts); httpx-based,
  JSON to stdout, `--text` mode strips envelope.
- **v2→v3 compat shim** (`src/agent_memory_lite/mcp/v2_compat.py`)
  — 26 v2 tool names mapped to v3 backends. Tier 1: full
  translation (write_decision/theory, get_object, upsert_concept/
  behavior/role/skill/playbook, archive, pin, search, list_*).
  Tier 3: `translation_pending` stub. Activation gate
  `MEMORY_V2_COMPAT_ENABLED` (default off until cutover); wired
  into MCP dispatch so v2 calls route through v3 backend when on.

### Docs (Phase 3)

- [`docs/V3_SCHEMA.md`](docs/V3_SCHEMA.md) — table-by-table reference
- [`docs/V3_AGENT_RUNTIMES.md`](docs/V3_AGENT_RUNTIMES.md) — wiring
  per agent (Claude Code, Cursor, Codex, Aider, CI)
- [`docs/V3_MIGRATION.md`](docs/V3_MIGRATION.md) — cutover playbook
- [`docs/V3_REMOVED.md`](docs/V3_REMOVED.md) — week-8 kill-list
  (~9,500 SLOC across 9 categories)

### Verification (Phase 4)

- **Brief cache** keyed on `(workspace_id, max_tokens, fingerprint)`
  where fingerprint = SHA1 of cardinal mutation timestamps across
  decisions/behaviors/tasks/code_digests/episodes. Automatic
  invalidation on any write; bounded to 16 entries with LRU eviction.
- **`scripts/v3_acceptance_gate.py`** — 4 measurements + pass/warn/
  fail verdict against plan targets: brief within_budget, cache
  hit rate ≥70%, median projection tokens ≤30, digest staleness
  ≤120 minutes.
- **Cutover round 1**: deleted 2,462 SLOC of one-off scripts
  (tier0_*, v2_1_followup, verify_v215, post_bug_fix_reset,
  crash_test_v3 monolith — replaced by modular `scripts/crash_test/`
  package).
- **Cutover round 2**: v2 compat shim wired into MCP dispatch behind
  `MEMORY_V2_COMPAT_ENABLED=true`. Zero behaviour change when off.

### Remaining for v3.0.0 final

- Migrate all 8 registered workspaces to v3 schema
- Acceptance gate measurements on real workspace data
- Drop remaining ~7,000 SLOC per `V3_REMOVED.md` after canary flip
- Flip canary workspace to `mode: v3`

## 2.0.0 — 2026-05-10

**First public-facing release.** Consolidates ~6 months of internal
incremental development (the legacy 1.x → 2.1.x → 2.2.x lineage) into
a single coherent baseline. Every feature listed below is on by
default; flag-off parity invariants in `tests/invariants/` lock the
v1.0.x byte-equivalent path for any operator who wants to peel a
layer off.

### What you get

- **Local-first memory substrate** — SQLite (WAL + FTS5) +
  LanceDB embedded vector index + sentence-transformers (CPU) +
  Ollama for the mandatory LLM extractor. No cloud calls. No Docker.
  Service binds to `127.0.0.1`. Hard guard at startup rejects any
  non-loopback URL or known cloud hostname.

- **Per-project workspace isolation.** Every project gets its own
  SQLite + LanceDB pair via `MEMORY_DB_PATH` and `VECTOR_DB_PATH`;
  `workspace_id` is the logical namespace inside that database.
  Strict project mode blocks writes to foreign workspaces; reads to
  any registered workspace stay open (asymmetric isolation).

- **Hub mode for shared services.** `scripts/serve.py` auto-enables
  hub mode when `~/.agent_memory/workspaces.json` lists at least one
  project; per-call routing through `X-Memory-DB-Path` headers lets
  one HTTP service serve every project chat without restart. Pass
  `--strict` to force single-workspace mode.

### Memory taxonomy

First-class objects with their own writers, ranking, and review
surface:

- **Episodes** — append-only audit log of agent actions. Every write
  flows through redaction (secrets stripped) before reaching SQLite
  or LanceDB.
- **Decisions** — committed architectural / operating choices with
  supersedes chains and `dependent_decision_ids` linkage.
- **Theories** — research hypotheses with claim, mechanism,
  predictions, and validation criteria. Anti-theories preserved as
  `status="rejected"` for reusable negative knowledge.
- **Snapshots / Experiments / Experiment results** — research-lab
  pipeline that auto-adjusts theory confidence/status and emits
  contradiction insights on refuting evidence.
- **Concepts** — shared vocabulary (gates, metrics, cohorts,
  artifacts) so future agents share the same terms.
- **Insights** — distilled lessons attached to theories / decisions /
  capabilities.
- **Roles / Skills / Playbooks** — reusable execution knowledge with
  usage / success / failure counters and 30-day decay.
- **Capability links** — explicit "this role/skill/playbook must
  influence this research object" contracts.
- **Behavior instructions** — high-trust persistent rules for
  communication style, project conventions, workflow preferences,
  and operating discipline. Conflict policies (`system_wins`,
  `current_user_wins`, `higher_priority_wins`, `most_specific_wins`,
  `latest_wins`).
- **Memory candidates** — review-queue for promotion / rejection;
  `kind=correction` candidates from the v1.10 correction-aware loop.
- **Code-memory** (separate substrate) — language-aware symbol +
  edge graph + signature-versioning + soft-edge similarity.

### Adoption-by-default — the four moves

The system is designed so that the agent doesn't need to remember
the discipline rules — the server defaults the right thing.

1. **Move 1 — auto-thread `source_episode_id`**.
   `memory_write_decision` and `memory_write_theory` auto-fill
   `source_episode_id` from the agent's most recent
   `memory_ingest_episode` (10-minute window, scoped by
   `X-Memory-Agent-Id`; anonymous fallback at 60s). Pass
   `allow_orphan: true` for deliberate untraced writes.
   Off-switch: `MEMORY_AUTOTHREAD_DECISION_SOURCE=false`.

2. **Move 2 — compound write tool `memory_record_with_evidence`**.
   Bundles `ingest_episode + write_decision + optional
   link_capability` into one atomic call. The agent writes the
   evidence + decision + capability link in one tool use instead of
   three.

3. **Move 3 — `capability_suggestions` on decision writes**.
   `memory_write_decision` and `memory_record_with_evidence`
   responses include a top-3 list of workspace capabilities
   (roles / skills / playbooks) ranked by token-overlap with the
   decision text. Read-only hint — the agent decides whether to
   call `memory_link_capability` with one of the suggestions.

4. **Move 4 — `capability_suggestions` on theory writes**.
   `memory_write_theory` returns the same shape; same contract.

   All four Moves work identically across the HTTP route, the MCP
   stdio handler, and the in-process MCP tool — local-fallback
   deployments keep the full surface even when the HTTP service is
   unreachable.

### Code-memory

The v1.4 → v2.1.x code-memory roadmap ships as a first-class
substrate alongside the memory substrate proper:

- `memory_find_symbols` — exact symbol-level lookup by qualified
  name or prefix; filters by symbol_kind and language.
- `memory_graph_neighbors` — hard-graph upstream / downstream by
  qualified_name or chunk_id, eight edge types
  (`calls`, `imports`, `exports`, `extends`, `implements`,
  `references`, `instantiates`, `decorated_by`).
- `memory_breaking_changes` — every symbol whose signature_hash
  changed in the last N days, paired with downstream caller count.
- `memory_file_digest` — narrative + structured digest for one
  file (chunk count, symbol kinds, edge counts, recent versions).
- `memory_code_overview` — workspace dashboard payload (counts,
  recent files, breaking changes, active edits, top-called).
- `memory_code_graph` — JSON for D3 visualization on `/ui/graph`.
- `memory_symbol_history` — every signature version of one symbol.
- `memory_soft_neighbors` — MinHash similar-signature soft graph
  across files and languages.
- `memory_claim_edit` / `memory_release_edit` /
  `memory_list_active_edits` — multi-agent edit coordination
  primitives.

### Correction-aware learning loop (was v1.10)

Closes the "operator corrects agent → lesson dies in chat" gap.
The `UserPromptSubmit` hook reads the Claude Code transcript JSONL,
finds the previous assistant turn, and ingests claim+correction as
two cross-referenced episodes when the current prompt matches a
heuristic (Russian + English regex, length floors, daily flood
cap). The new `CorrectionExtractor` (registered alongside
`HeuristicExtractor` in `auto_promote._build_extractors`) emits a
`memory_candidate(kind=correction)` for review. Operator promotes
via `POST /memory/promote_candidate_to_behavior` — the resulting
`behavior_instruction` lands in every future
`<behavior_instructions>` envelope. Trust gate intact; auto-promote
forbidden.

Operator runbook: [`docs/V1_2_0.md`](docs/V1_2_0.md).

### Quality features (default ON, calibrated)

Every quality flag below defaults ON in `Settings`. Calibration
evidence in [`docs/V1_1_0_CALIBRATION.md`](docs/V1_1_0_CALIBRATION.md);
operator runbook in [`docs/V1_1_0.md`](docs/V1_1_0.md).
Flag-off parity invariant locks the v1.0.x baseline byte-for-byte.

- **Episode dedup** (`MEMORY_EPISODE_DEDUP_*`) — cosine ≥0.92
  against recent window skips writes.
- **Confidence decay** (`MEMORY_CONFIDENCE_DECAY_*`) — exponential
  age decay on chunk hit scores so old episodes don't out-rank
  recent ones.
- **Auto-conflict detection** (`MEMORY_CONFLICT_DETECT_*`) —
  emits `potential_conflict` events for decisions/theories with
  Jaccard overlap ≥0.6.
- **Feedback-aware scoring + implicit feedback**
  (`MEMORY_FEEDBACK_EWMA_*`, `MEMORY_IMPLICIT_FEEDBACK_*`) — closes
  the EWMA loop with operator-action-derived feedback.
- **Capability maturity + behavior tracking**
  (`MEMORY_CAPABILITY_MATURITY_*`,
  `MEMORY_BEHAVIOR_APPLY_TRACKING_*`).
- **Cold-memory lifecycle** (`MEMORY_COLD_*`) — stamps
  `last_retrieved_at` on top-K returned ids; emits `cold_candidate`
  events for rows untouched > 60 days.
- **Theory → decision-candidate bridge** (`MEMORY_THEORY_BRIDGE_*`)
  — validated theories with ≥3 evidence rows surface as decision
  candidates.
- **Reflective compaction** (`MEMORY_REFLECTIVE_COMPACT_*`) —
  Ollama pass over recent episodes proposes lessons into
  `insight_candidates`.
- **Hygiene recurrence + sentinel autorun**
  (`MEMORY_HYGIENE_PERSIST_*`, `MEMORY_SENTINEL_*`) — findings
  persisted with recurrence counts; per-workspace lock prevents
  duplicate concurrent daemons.
- **Pending-review envelope** — every `memory_get_context`
  injects a `<pending_review>` block when candidates are pending.

### `<memory_context>` envelope

The auto-injection hook (`scripts/inject_memory_context.py`) emits
a single XML envelope ahead of every user prompt. Sections, in
priority order: `<core_memory>`, `<behavior_instructions>`,
`<task_state>`, `<active_decisions>`, `<active_theories>`,
`<research_agenda>`, `<agent_capabilities>`, `<procedural_rules>`,
`<retrieved_facts>`, `<retrieved_chunks>`, plus
`<pending_review>` when populated.

Discover-then-fetch pattern: each section renders top-N items in
full; the rest appear as compact `<ref id="..."/>` entries inside
an `<index>` block. The agent calls `memory_get_object(kind, id)`
to expand any ref that matters.

When the host console codepage doesn't speak UTF-8 (Windows
cp1251 / cp1252), the hook reconfigures stdout/stderr to UTF-8 with
`errors="replace"` so an em-dash, arrow, or Cyrillic char does not
crash with `UnicodeEncodeError` mid-envelope. When the cwd Claude
Code reports does not match any registered project_root, the hook
falls back to an auto-bootstrapped `global` workspace AND emits a
visible `<hook_notice severity="warn" code="global_fallback">`
block listing registered workspaces and three recovery paths so
the agent / operator can fix the routing rather than stare at an
empty envelope.

### Browser UI

The HTTP service serves five UI pages (Observatory `/ui`, Code
`/ui/code`, Graph `/ui/graph`, Review `/ui/review`, Browse
`/ui/browse`) sharing a workspace dropdown via
``ui/app_header.js``. Every page attaches the
``X-Memory-DB-Path`` and ``X-Memory-Vector-Path`` headers to
its API calls so hub-mode routing lands on the right physical
DB regardless of which workspace the operator selects. The
review queue gates the promote button by candidate kind:
``decision`` / ``procedural_rule`` / ``constraint`` get the
inline promote, ``correction`` gets the
behavior-instruction modal, every other kind shows a single
reject button (descriptive extraction noise has no durable
target).

### Operations

- HTTP service: `python -m agent_memory_lite` (or
  `python scripts/serve.py` for hub-mode auto-detect).
- Pre-push gate: `python -m scripts.crash_test --skip-llm` runs
  27 phases / 133+ assertions on an isolated `qa-crash-test`
  workspace; `bash scripts/install_hooks.sh` wires it into
  `git push`.
- Operator runbook: [`docs/OPERATIONS.md`](docs/OPERATIONS.md).
- Code-memory operator guide:
  [`docs/CODE_MEMORY_GUIDE.md`](docs/CODE_MEMORY_GUIDE.md).
- Calibration evidence: [`docs/V2_CALIBRATION.md`](docs/V2_CALIBRATION.md).

### Quality gates at 2.0.0 ship

- `ruff check` + `ruff format --check` — clean across 819 files.
- `mypy` strict — clean across 536 source files.
- `check_sloc.py --enforce` — every file at or below the 150-SLOC
  ceiling except `config/settings.py` (152, grandfathered with
  documented composed-Settings decomposition plan).
- `pytest -q` — **971 cases pass**.
- Crash test — **27 phases, 133/133 assertions** (1 skip — Ollama
  optional path, gracefully degraded).
- `scripts/run_evals.py --no-vector` — 13/13 cases pass,
  recall@10 = 1.0, precision@10 = 1.0, secret_leak_count = 0,
  prompt_injection_failures = 0.

### Migration from internal pre-2.0.0 development

Operators who tracked the incremental 1.x → 2.1.x → 2.2.x branches
already have a working memory.db on the latest schema. `git pull` +
restart the HTTP service + restart Claude Desktop / Cursor / VS
Code (so MCP stdio servers pick up the new code) is the only
upgrade step. Every flag carried forward; no schema change beyond
those listed in `migrations/`. The legacy CHANGELOG entries are
preserved verbatim in
[`docs/CHANGELOG_LEGACY.md`](docs/CHANGELOG_LEGACY.md) for historical
trace.
