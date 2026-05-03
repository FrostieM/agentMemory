# Changelog

All notable changes to agent-memory-lite. Versions follow semver — minor
bumps add functionality (and may flip a default), patch bumps fix bugs
without behaviour change.

## 1.1.3 — 2026-05-04

**Patch.** Hub-mode dispatch on legacy-schema DBs no longer 500s.

When the HTTP service is in hub mode and a hook routes a request to a DB
that pre-dates the v1.1.0 migrations (e.g. an auto-bootstrapped
`~/.agent_memory/global/` DB created by an earlier setup, or any v1.0.x
workspace registered in the workspaces.json registry), three post-build
hooks would query columns/tables that didn't exist on that DB and
return HTTP 500.

Affected paths (all defaults ON since 1.1.0, all running on every
get_context call):

* `retrieval/pending_review.py:load_pending_review` queried
  `decision_candidates` (migration 0023) and `insight_candidates`
  (migration 0024) — `no such table` on legacy DBs.
* `retrieval/last_retrieved_tracker.py:_update_kind` updated
  `last_retrieved_at` (migration 0022) — `no such column` on legacy DBs.
* `capability/behavior_apply.py:mark_behavior_instructions_applied`
  updated `application_count` + `last_applied_at` (migration 0021) —
  `no such column` on legacy DBs.

Fix: each path now catches `sqlite3.OperationalError` and degrades to a
no-op (returns 0 rows / empty summary). Hub mode keeps serving — the
v1.4-v1.9 + v2 features that depend on the new schema simply skip on
legacy DBs, which is the correct semantics: those features were never
flag-active for those DBs anyway.

Tests: 1 new case in `tests/unit/retrieval/test_pending_review.py` —
`test_load_pending_review_handles_legacy_schema` — opens an empty DB,
confirms `load_pending_review` returns an empty summary instead of
raising. Total pytest count: 654 → 655.

No schema changes, no migrations, no flag flips. Pure resilience patch
covering the hub-mode legacy-DB edge case.

## 1.1.2 — 2026-05-04

**Patch.** UserPromptSubmit hook FTS-only fallback when HTTP service down.

Pre-1.1.2 the auto-injection hook was HTTP-only: if the service at
`127.0.0.1:8765` was down, every prompt got an empty `<agent-memory>`
notice and the agent ran blind on memory. MCP stdio tools have always
had a local fallback (open SQLite directly, run build_context); the
hook didn't. Now they're symmetric.

Fixes:

* New `scripts/inject_memory_fts_fallback.py` — opens SQLite directly,
  runs FTS on chunks_fts plus structured-section reads from
  `core_memory` / `behavior_instructions` / `decisions`, renders a
  minimal envelope. No embedding model load (would be 2-3s cold start,
  unacceptable per-prompt). No graph walk, no EWMA re-rank — degraded
  but useful.
* `scripts/inject_memory_context.py` catches `httpx.ConnectError` and
  falls back to the FTS module before emitting the notice. Hook stays
  fast (<100ms) and never leaves the agent without context when the
  registered DB is reachable.
* Output XML matches a subset of the full envelope so the agent treats
  fallback identically to the HTTP path: `<core_memory>` /
  `<behavior_instructions>` / `<active_decisions>` / `<retrieved_chunks>`.
* Trade-off documented: vector ranking + RRF + graph walk skipped.
  Quality lower than HTTP path but correct sections rendered.

Tests: 8 new cases in `tests/unit/scripts/test_inject_memory_fts_fallback.py`
covering FTS query sanitisation (special chars, short tokens, capping),
graceful failure on missing DB, full envelope rendering against a
seeded workspace, XML escaping of `<`/`&`/`>` in stored content. Total
pytest count: 646 → 654.

Asymmetry resolved:

| Surface | Pre-1.1.2 | Post-1.1.2 |
|---------|:---------:|:----------:|
| MCP stdio tools | HTTP delegate → local fallback | unchanged |
| Auto-injection hook | HTTP only — fails silent | HTTP → FTS fallback → notice |

No schema changes, no migrations, no flag flips. Pure resilience patch.

## 1.1.1 — 2026-05-04

**Patch.** MCP function-call markup guard.

When an agent invoked any `memory_*` MCP tool while accidentally embedding
its own function-call boundary tags (`</decision_text>`,
`<parameter name="...">`, `</invoke>`, etc.) inside the textual content of
a parameter, that markup was persisted verbatim into SQLite. The UI
rendered the garbage tail honestly, but the affected fields contained
trailing structural noise that polluted retrieval and review.

Fixes:

* `src/agent_memory_lite/redaction/mcp_markup.py` (new) — `strip_mcp_markup`
  truncates at the first occurrence of any strict MCP marker; idempotent on
  clean input. `extract_rationale` recovers the leaked
  `<parameter name="rationale">...</parameter>` block when a write put both
  decision_text and rationale into a single value.
* `src/agent_memory_lite/api/schemas/_text_guard.py` (new) —
  `SafeText` / `SafeTextOptional` pydantic annotations apply the strip via
  `AfterValidator` to every text field on every write surface.
* Annotated text fields:
  * `WriteDecisionRequest`: title, decision_text, rationale.
  * `IngestEpisodeRequest`: raw_text, summary.
  * `WriteTheoryRequest`: title, claim, mechanism, experiment_plan.
  * `UpsertBehaviorInstructionRequest`: name, rule, rationale.
  * `UpsertConceptRequest`: name, definition.
  * `DistillInsightRequest`: summary, proposed_action.
  * `UpsertAgentRoleRequest` / `SkillRequest` / `PlaybookRequest`: name,
    purpose / summary / goal.
* `scripts/repair_text_artifacts.py` (new) — one-shot cleanup tool that
  walks every text column and applies the same strip + rationale recovery.
  Idempotent.
* Strict markers only — generic angle brackets (`<repo>`, `<=`, `<name>`,
  `<agent_capabilities>` and similar legitimate operator content) pass
  through untouched.

Existing data repaired:
* `agentLight` workspace: 4 rows (2 decisions + 1 episode + 1 chunk),
  rationale recovered for 2 decisions where the leak hid the
  rationale block inside decision_text.
* `copyBot` workspace: 74 rows (6 decisions + 12 episodes + 12 chunks +
  9 insights + 13 concepts + 14 behavior_instructions), rationale
  recovered for 6 decisions.

Tests: 13 new unit + property tests (`tests/unit/redaction/test_mcp_markup.py`)
covering idempotency, earliest-marker-wins, generic-bracket pass-through,
rationale extraction with terminated and unterminated blocks. Total
pytest count 633 → 646.

No schema changes, no migrations, no flag flips. Pure preventive guard
plus existing-data sweep.

## 1.1.0 — 2026-05-03

**Headline:** six feedback loops (v1.4 through v1.9) plus three follow-on
improvements (v2.1 / v2.2 / v2.3) ship default ON. Every flag flips off
via a single explicit `false` in `.env`; `tests/invariants/test_v2_parity.py`
locks the flag-off path as byte-equivalent to v1.0.x.

### Difference vs 1.0.0

| Surface | 1.0.0 | 1.1.0 |
|---------|-------|-------|
| **Test count** | ~360 | **633** (+273) |
| **Migrations** | 0001 (consolidated) | 0001 + 0020-0025 |
| **Default flags** | every quality flag OFF | every quality flag **ON** (calibrated) |
| **Scoring formula** | semantic+keyword only (other terms hardcoded 0) | full formula with importance/recency/confidence/feedback_ewma/graph |
| **Operator feedback** | manual `record_usage_feedback` only | derived automatically from archive/promote/link |
| **Capability counters** | static `confidence` only | usage / success / failure / last_invoked tracked |
| **Behavior application** | static rule list | `application_count` advances on each render |
| **Cold detection** | manual audit only | automatic `last_retrieved_at` + `cold_candidate` events |
| **Theory promotion** | manual decision_write | validated theories surface as `decision_candidates` (review-only) |
| **Compaction** | text-digest only | optional Ollama lesson extraction → `insight_candidates` |
| **Hygiene findings** | ephemeral (per-request) | persisted with recurrence counts |
| **Sentinel runs** | external cron only | trigger-on-traffic background scheduler |
| **Pending queue surface** | separate `/memory/list_candidates` call | inline `<pending_review>` envelope block |
| **MCP-vs-HTTP parity** | MCP local fallback skipped post-build hooks | single chokepoint (`apply_post_build_hooks`) called by both |

### What 1.1.0 adds (env-flag map; every default ON)

**v1.4 feedback-aware scoring** — completes the retrieval scoring formula
with a feedback-EWMA term over decisions / theories / chunks. New module
`retrieval/feedback_aggregator.py`. `MEMORY_FEEDBACK_EWMA_ENABLED=true`,
halflife 14d, self-loop guard, per-day-per-source cap of 10.
**Migration 0020** adds `feedback_ewma` column + `memory_usage_feedback.source`.

**v1.5 capability maturity + behavior tracking** — usage / success
counters on `agent_skills` / `agent_roles` / `agent_playbooks`;
`behavior_instructions.application_count` advances on each render. New
modules `capability/usage_tracker.py` + `capability/maturity.py` +
`capability/behavior_apply.py`. `MEMORY_CAPABILITY_MATURITY_ENABLED=true`,
`MEMORY_BEHAVIOR_APPLY_TRACKING_ENABLED=true`. **Migration 0021**.

**v1.6 cold-memory lifecycle** — `last_retrieved_at` stamping on top-K
retrieval (batched audit, batch_size=100); cold scanner emits
`cold_candidate` maintenance events for rows untouched > 60 days. Two-flag
split: tracking + auto-queue. `MEMORY_COLD_TRACKING_ENABLED=true`,
`MEMORY_COLD_AUTO_QUEUE_ENABLED=true`. **Migration 0022**.

**v1.7 theory → decision_candidates bridge** — validated theories with
≥ 3 supporting evidence rows surface as pending decision candidates.
Trust gate intact: never auto-promotes. `MEMORY_THEORY_BRIDGE_ENABLED=true`,
`MEMORY_THEORY_BRIDGE_MIN_EVIDENCE=3`. **Migration 0023** adds
`decision_candidates` table.

**v1.8 reflective compaction** — `/memory/compact` runs an Ollama pass
over recent episodes and proposes lessons into `insight_candidates`.
Lesson must cite ≥ 4 source episodes; cap 10 per run. Gracefully degrades
when Ollama unreachable. `MEMORY_REFLECTIVE_COMPACT_ENABLED=true`.
**Migration 0024** adds `insight_candidates` table.

**v1.9 hygiene recurrence + sentinel persistence** — hygiene findings
and watchdog runs persisted with recurrence counts.
`MEMORY_HYGIENE_PERSIST_ENABLED=true`,
`MEMORY_SENTINEL_PERSIST_ENABLED=true`. **Migration 0025** adds
`recurrence_count` / `first_seen_at` / `last_seen_at` on
`maintenance_events` + `retrieval_sentinel_results` table.

**v2.1 implicit feedback** — derives `memory_usage_feedback` rows from
existing operator actions (archive=-1.0/source=`implicit_archive`,
`promote_candidate`=+0.7/`implicit_promote`, `link_capability`=strength
clamped [0,1]/`implicit_link`). Wired in `api/routes/archive.py` +
`candidates.py` + `capability_links.py`. Closes the loop where v1.4 EWMA
stayed at 0 because `record_usage_feedback` was almost never called.
`MEMORY_IMPLICIT_FEEDBACK_ENABLED=true`.

**v2.2 pending_review envelope** — every `memory_get_context` envelope
injects a `<pending_review>` XML block when `decision_candidates` or
`insight_candidates` are pending. Each `<ref>` carries `id`, `kind`,
`title`, `theory_id` (for decision candidates) — every field an agent
needs to call promote / reject without a separate
`/memory/list_candidates` round-trip. Data-driven: block appears only
when source rows exist.

**v2.3 trigger-on-traffic sentinel scheduler** — every `get_context`
triggers a background sentinel pass when overdue. Per-workspace
`threading.Lock` (`maintenance/sentinel_lock.py`) prevents duplicate
concurrent daemons. Hub-mode aware: `db_path` resolved from the
request-scoped connection via `PRAGMA database_list`, not the singleton
`settings.db_path`. `MEMORY_SENTINEL_AUTORUN_HOURS=6.0` default.

### Architecture chokepoint

`apply_post_build_hooks()` in `api/routes/context_post_build.py` is the
single entrypoint for the four context post-build hooks (behavior_apply
tracking, last_retrieved stamping, sentinel scheduler,
pending_review injection). Both the HTTP route
(`api/routes/context.py`) and the MCP stdio local fallback
(`mcp/stdio_handlers_episodes.py`) call it. **Pre-fix MCP-only
deployments silently lost v1.5 / v1.6 / v2.2 / v2.3** on every read.

### Calibration evidence

Replayed 1370 audit_log + capability_links rows from a real copyBot
workspace into 158 implicit feedback rows; 95% rank churn (61 / 64 active
decisions changed position); high-EWMA cohort rose +0.84 places, low-EWMA
cohort dropped 26 places, biggest faller -51 positions for a
high-importance decision with zero operator interaction. Half-life sweep:
identical results across {1, 3, 7, 14, 30, 60} days because backdated
feedback spans only 5 days — keep default 14d, re-sweep after 30+ days
of live writes. Regression injection: archived 1 of 3 active decisions,
sentinel detected delta +1 with no spurious failures.

Reproduce on any post-1.4 workspace via
`scripts/calibration/{replay_implicit_feedback,ab_compare_decisions,
halflife_sweep,regression_injection}.py` (each takes
`--db <path> --workspace <id>`). Full report:
`docs/V1_1_0_CALIBRATION.md`.

### Quality gates

* `pytest -q` — 633 passed (was 491 in 1.0.3 baseline).
* `ruff check` + `ruff format --check` — clean across 661 files.
* `mypy src` — strict, 0 issues across 430 source files.
* `python scripts/check_sloc.py --enforce` — every `src/**/*.py` ≤ 150 SLOC.
* Crash test (`scripts/crash_test`, 26 phases / 120 assertions) — PASS.

### Upgrade path

Migrations 0020-0025 apply automatically on first connection
(`db/migrations.py:apply_migrations`). New columns get neutral defaults
(`feedback_ewma=0.0`, `last_retrieved_at=NULL`, `usage_count=0`, etc.) so
retrieval ranking stays unchanged until the new write paths populate them.
Backwards-compatible: older code ignores new columns. To restore the
v1.0.x baseline, set the corresponding env var to `false` / `0.0` in
`.env` — `tests/invariants/test_v2_parity.py` guarantees byte-equivalence.

---

## 1.0.3 — 2026-04-30

**Patch.** Idempotent agent-contract sync.
`scripts/setup_agent.py:upsert_contract` is now byte-stable across reruns:
`render_contract_block()` produces the same canonical block whether the
file is being created or updated, and the end-marker search uses `rfind`
so the replaced span runs from the FIRST `:begin` to the LAST `:end`. A
hand-broken anchor file with stray duplicate `:end` markers is healed in
a single sync pass instead of silently accumulating drift.

## 1.0.2 — 2026-04-29

**Patch.** Single-source agent contract.
`docs/AGENT_CONTRACT.md` is now the canonical body for the agent operating
contract. `CLAUDE.md` and `AGENTS.md` carry the same body verbatim between
`<!-- agent-memory-lite-contract:begin/end -->` markers. CI runs the same
sync and `git diff --exit-code -- CLAUDE.md AGENTS.md`, so any direct edit
to the marker block in the anchor files (without syncing the canonical)
fails CI.

## 1.0.1 — 2026-04-28

**Patch.** UI live-write refresh fix + action-colored spokes.

* `/ui` no longer goes stale when a new row is written — `graph_delta`
  handler invalidates the per-family detail cache and re-fetches if the
  inspector is currently open on that family.
* Spoke + object node tint encodes the action: created / upserted /
  restored = green, pinned = yellow-green, unpinned = amber, archived /
  superseded = red-orange, deleted / rejected = red, reads keep the
  family hue.

## 1.0.0 — 2026-04-26

**Initial stable release.**

* 18+ persistence kinds (episodes, chunks, files, decisions, theories,
  experiments, snapshots, research_insights, domain_concepts,
  agent_roles / skills / playbooks, capability_links,
  behavior_instructions, core_memory, task_state, procedural_rules,
  entities, facts, audit_log, memory_candidates, maintenance_events,
  memory_state_snapshots, vector_index_metadata, memory_usage_feedback,
  workspace_manifest, workspace_meta).
* RRF fusion of FTS BM25 + vector cosine, graph walk for entity facts,
  token-budget cap, discover-then-fetch index blocks, pinned-first
  ordering for decisions / behavior_instructions / core_memory.
* Operator surface: pin / archive / what_references / list_audit /
  snapshot_save+list+diff / review_queue / compact_trigger; integrity
  audit, hygiene report, quality gate, candidate triage.
* Hub mode + asymmetric isolation: one service serves many projects via
  `~/.agent_memory/workspaces.json`; reads stay loose, writes stay strict
  per-project.
* Live observatory at `/ui` with burst-coalesced animation cycles.
* Memory-quality features (env-flagged, **off** by default in 1.0.x —
  flipped to default ON in 1.1.0): episode dedup, confidence decay, auto
  conflict detection, token-aware compaction watchdog.
