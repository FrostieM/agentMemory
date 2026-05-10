# Changelog

All notable changes to **agent-memory-lite**. This file starts at the
2.0.0 baseline; the incremental 1.x → 2.1.x → 2.2.x development
history that produced 2.0.0 is preserved verbatim in
[`docs/CHANGELOG_LEGACY.md`](docs/CHANGELOG_LEGACY.md).

Versioning follows semver from 2.0.0 onward. Minor bumps add
functionality (and may flip a default), patch bumps fix bugs without
behavioural change.

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
