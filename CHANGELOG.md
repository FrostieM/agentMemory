# Changelog

All notable changes to agent-memory-lite. Versions follow semver — minor
bumps add functionality (and may flip a default), patch bumps fix bugs
without behaviour change.

## 1.2.3 — 2026-05-05

Closes the structural cause of the "decisions and theories without
capability link" debt observed across long-running workspaces (copyBot
hit 53 missing-link findings on ~150 research objects in a week despite
having 12 roles + 35 skills + 15 playbooks defined). The root cause was
discipline drift: agents wrote decisions/theories but skipped the
follow-up `memory_link_capability` call. The fix is structural — the
neutral project-memory seed now writes one project-AGNOSTIC discipline
`behavior_instruction` that lands in every workspace's
`<behavior_instructions>` envelope, so the next agent reads the rule
before the first write of the session.

### Added

- `src/agent_memory_lite/bootstrap/project_memory_seed_behavior.py` —
  new module hosting `link_capability_discipline_instruction()`. Split
  out of `project_memory_seed_templates.py` to keep both files under
  the 150-SLOC ceiling and to make the "where to add new generic
  discipline rules" location explicit. Each rule must be project-
  AGNOSTIC (no language, personality, or project-specific behavior);
  project-specific behavior_instructions remain operator-driven via
  `memory_upsert_behavior_instruction`.
- `link_capability_discipline_instruction(workspace_id, source_episode_id)`
  factory returns a `BehaviorInstructionIn` with:
  - name: "Link capability after every decision and theory write"
  - kind: `operating_rule`
  - scope: `workspace`
  - priority: `user_preference`
  - conflict_policy: `current_user_wins` (operator can always override)
  - applies_to: `[memory_write_decision, memory_write_theory,
    memory_add_theory_evidence, memory_write_experiment]`
  - source_type: `seed_bootstrap`
- 2 new tests in `tests/test_project_memory_seed.py` plus updates to
  the 2 existing tests so the seed write of one BI is locked in.

### Changed

- `src/agent_memory_lite/bootstrap/project_memory_seed.py` —
  `seed_neutral_project_memory()` now calls
  `upsert_behavior_instruction()` after seeding skills/playbook/concepts.
  `ProjectMemorySeedResult.behavior_instructions` is a new list field;
  `behavior_instructions_written` is now a derived property
  (kept for backward-compat with operators reading the JSON output).
- `docs/AGENT_CONTRACT.md` — doctrine clarified: seed may write
  generic discipline `behavior_instructions`; project-specific
  language / style / personality rules remain operator-driven. Block
  re-injected into all five contract surfaces (agent-mem CLAUDE.md,
  AGENTS.md; copyBot CLAUDE.md, AGENTS.md; `~/.claude/CLAUDE.md`).
- `bootstrap/project_memory_seed_templates.py::memory_bootstrap_playbook` —
  one of its `success_criteria` lines was "No behavior instruction
  was seeded"; refined to reflect the new doctrine.

### Notes

- **Seed is idempotent.** Re-running `setup_agent.py --project <path>`
  on an existing workspace does NOT duplicate the BI; upsert keys on
  `(workspace_id, name)`. Existing manually-created behavior_instructions
  in the workspace are untouched.
- **Existing workspaces don't auto-upgrade.** The new BI lands only
  when the seed runs (next `setup_agent.py --project ...` without
  `--no-seed-memory-bootstrap`, OR a fresh project init). Live in
  copyBot 2026-05-05: ran the seed against the production memory.db
  → BI inserted at `beh_758aa5cdd7987304`,
  hygiene findings 105 → 43 (-59%), quality_gate findings 93 → 32
  (-66%), all 9 noise CORRECTION candidates rejected, 60 important
  decisions backfilled with provenance, 57 capability_links applied
  via auto-triage.
- **Operator override remains supreme.** A workspace that wants
  stricter or laxer discipline rules can `memory_archive` this BI
  or upsert a replacement; `current_user_wins` policy guarantees
  any explicit operator instruction in the same session takes
  precedence.

## 1.2.2 — 2026-05-05

Patch release — closes a heuristic false-positive in the v1.10
correction loop that surfaced after a day of real-world use.

### Fixed

- **Correction heuristic produced noise on Claude Code system blocks**
  (`extraction/correction_patterns.py`). Live regression observed
  in copyBot 2026-05-05: a single afternoon produced 6 candidates
  with subject `Verify before claiming: <task-notification>` —
  Claude Code wraps runtime tool/notification output in
  `<task-notification>...`, `<command-name>...`, `<ide-selection>...`,
  `<system-reminder>...`, `<command-message>...` tags before they
  reach the prompt. The heuristic was matching on text inside these
  wrappers as if it were a user correction. Added
  `SYSTEM_BLOCK_OPENER` regex; `match_correction()` short-circuits
  to `matched=False` when the message starts with `<tag>`. Inline
  tag mentions (e.g. "use the `<task-notification>` markup") are
  unaffected because the regex requires the tag at message start.

### Added

- `tests/unit/extraction/test_correction_patterns.py` — 6 new tests:
  5 system-block false-positive cases (`task-notification`,
  `command-name`, `ide-selection`, `system-reminder`, system-block
  with correction body inside) all asserting no match, plus 1
  positive test that an inline tag mention in a real correction
  still matches.

### Notes

- This is a heuristic-only change. The same noise also appeared in
  copyBot's queue from a SEPARATE path: the LLM (Ollama) extractor
  produced 3 CORRECTION-kind candidates from an `agent_action`
  episode (Phase 7.A.1 implementation report) by reading audit-
  findings phrasing as "fixed issues". That separate path is NOT
  covered by this patch — see roadmap for future v1.x work to
  constrain LLM-extractor's CORRECTION semantics.
- Operator playbook for cleaning the existing copyBot queue: reject
  all 9 pending CORRECTION candidates (none are real corrections of
  agent claims), restart HTTP service + MCP stdio to pick up 1.2.2.

## 1.2.1 — 2026-05-04

Hardening patch on top of 1.2.0. A four-AI-agent post-release audit
found one critical and three high-severity issues in the just-shipped
correction-promotion surface. All four are fixed; each carries a paired
regression test so the gap can't come back.

### Fixed

- **CRITICAL — Atomic promotion** (`ingestion/correction_promotion.py`).
  Pre-1.2.1, `promote_correction_to_behavior` made three independent
  commits: behavior_instruction upsert, optional pin, candidate flip +
  audit. A failure mid-flow could leave a behavior_instruction live
  but the candidate stuck at `status='new'`, producing a confusing
  `409 name_taken` on retry. The three steps now run inside one
  outer `with_tx`. Inner helpers' own `with_tx` calls become
  SAVEPOINTs (see the `db/transactions.py` change below). On any
  failure the entire promotion rolls back and the operator can retry
  cleanly.
- **HIGH — `coerce_applies_to` accepted tuple silently dropping it
  to empty** (`ingestion/correction_promotion_guards.py`). The old
  `isinstance(value, list)` check rejected an already-coerced tuple
  and returned `()`. Now accepts both list and tuple, returns `()`
  for `None`, and raises `TypeError` on unknown types (dict, int,
  …) so bad input surfaces as a 422 at the boundary rather than a
  silent empty list.
- **HIGH — `record_throttle_rejection` called `conn.commit()` on the
  shared connection** (`extraction/correction_distill.py`). In the
  current call graph the commit was a no-op, but if any future
  refactor invokes the throttle helper from inside a `with_tx`
  block, the explicit commit would have prematurely finalized the
  outer transaction. Removed; in autocommit mode each `insert_audit`
  statement is its own implicit transaction so the throttle row
  still persists.
- **MEDIUM — Hook `transcript_path` had no allowlist**
  (`scripts/transcript_pair_extractor.py`). A compromised or
  misconfigured hook caller could pass an arbitrary readable path
  (`/etc/passwd`, another user's transcript) and have its tail
  bytes ingested as the agent claim. Now restricted to
  `~/.claude/` plus directories listed in
  `AGENT_MEMORY_TRANSCRIPT_ROOTS` (os.pathsep-separated). Path
  resolution rejects traversal attacks via `Path.resolve()` +
  `relative_to()`.

### Changed

- `db/transactions.py` — `with_tx` is now nest-safe. When entered
  while an outer transaction is already open, it issues a SAVEPOINT
  instead of a nested `BEGIN` (which SQLite forbids). Top-level
  callers see no behaviour change. This is what makes the atomic
  promote possible without rewriting `upsert_behavior_instruction`
  and `pin_memory_object`. RELEASE is now in a `finally` with
  `contextlib.suppress(sqlite3.Error)` so the savepoint is always
  cleaned up even if the rollback path itself errors. Token width
  upgraded from 4 to 8 bytes for collision safety in deeply nested
  batch jobs.
- `ingestion/pin_service.py` — `_set_table_pinned` now wraps the
  UPDATE in `with_tx` instead of an explicit `conn.commit()`. Top
  level still BEGIN/COMMITs; nested inside the atomic promote
  becomes a SAVEPOINT.
- `repositories/decisions_repo.py` — `set_decision_pinned` migrated
  to `with_tx` for symmetry with `pin_service._set_table_pinned`.
  Pre-1.2.1 the helper called `conn.commit()` directly; while no
  current code path pins a decision from inside an outer
  transaction, the asymmetry was a future foot-gun. Top-level
  callers see no behaviour change.

### Added

- `tests/unit/ingestion/test_correction_promotion.py` — 11 regression
  tests covering: atomic rollback on Step-1 / Step-2 / Step-3
  failure, happy-path end-to-end, `coerce_applies_to`
  tuple / dict / None / mixed list, throttle helper not committing
  outer tx, `with_tx` savepoint nesting, savepoint rollback on inner
  failure, `wrong_kind` guard still fires pre-write.
- `tests/unit/scripts/test_transcript_pair_extractor.py` — 3 new
  tests for the M3 allowlist (rejects path outside `~/.claude/`,
  rejects relative paths in env override, respects absolute env
  override) plus an autouse fixture so the existing 9 tests
  continue to pass against `tmp_path`.

### New env var

- `AGENT_MEMORY_TRANSCRIPT_ROOTS` (optional, default empty) —
  os.pathsep-separated **absolute** paths. Add to extend the
  transcript-read allowlist beyond the built-in `~/.claude/`.
  Relative entries are silently dropped (cwd is non-deterministic
  across hook fork points).

### Notes

- M3 is technically a behaviour change for hook callers that
  previously passed transcript paths outside `~/.claude/` (e.g.
  integration test harnesses pointing at `/tmp/`). The
  `AGENT_MEMORY_TRANSCRIPT_ROOTS` override re-allows specific
  external roots so existing test setups can adapt without code
  changes.

## 1.2.0 — 2026-05-04

**Headline:** v1.10 correction-aware learning loop. When the operator
corrects the agent's claim in chat, the system now captures the pair
automatically, proposes a one-line behavior fix, and queues it for
operator review. Promoted candidates land in
`<behavior_instructions>` and surface in every future envelope — so
the next session reads the rule before answering and tunes its caution.

This closes the **structurally missing loop** observed in 1.1.1: the
agent saw user corrections, fixed the immediate thing, then forgot
the lesson. v1.10 makes the loop automatic for the highest-frequency
operator action — correcting the agent — turning *"memory shapes
behavior via operator-trace"* from a README claim into an actual
mechanism.

See [`docs/V1_2_0.md`](docs/V1_2_0.md) for the operator runbook,
heuristic patterns, env-flag map, episode-dedup bypass rationale, and
the full validation matrix.

### Architecture (three stages, all behind one master flag)

1. **Capture** (`scripts/inject_memory_context.py` UserPromptSubmit hook)
   – Reads the Claude Code transcript JSONL referenced by `transcript_path`,
     locates the most recent assistant text turn within
     `MEMORY_CORRECTION_PAIR_WINDOW_MIN` minutes (default 30).
   – If the current user prompt matches the correction heuristic
     (regex over Russian + English contradiction patterns), ingests
     two episodes back-to-back: the agent claim
     (`metadata.kind=correction_target`) and the user correction
     (`metadata.kind=user_correction` with
     `correction_target_episode_id` cross-reference).
2. **Extract** (`src/agent_memory_lite/extraction/correction_extractor.py`)
   – New `Extractor` registered alongside `HeuristicExtractor` and
     the Ollama LLM extractor; runs on every ingest.
   – On a `user_correction` episode, looks up the paired claim,
     distills a one-line behavior rule via template (`Verify before
     claiming: …`), emits a `MemoryCandidate(kind=CORRECTION)` with
     0.5 / 0.7 / 0.85 confidence based on regex specificity.
3. **Promote** (`POST /memory/promote_candidate_to_behavior`)
   – Operator-driven, never auto-fires. Calls
     `upsert_behavior_instruction` with `source_type="memory_candidate"`,
     `source_id=<candidate.id>` so lineage is preserved. Updates
     candidate to `status='promoted'` with `promoted_target_*` filled.

### Added

- `src/agent_memory_lite/extraction/correction_patterns.py` — regex
  pattern set with bilingual openers (`нет, ` / `no, ` / `wait,` /
  `actually,` / `неправильно` / `я буквально` / `i literally`) and
  body markers (`не мог`, `это не так`, `that doesn't`,
  `you're wrong`, `cant/can't`). Plus a negative filter for
  agreement-with-negation phrases (`нет проблем`, `no problem`).
- `src/agent_memory_lite/extraction/correction_extractor.py` —
  `CorrectionExtractor(conn)` Extractor protocol implementation.
  Includes a per-workspace per-day throttle, workspace_id-scoped
  claim resolution (security), and audit-traced throttle rejections.
- `src/agent_memory_lite/extraction/correction_distill.py` —
  pure-function helpers (`distill_rule`, `clip`,
  `count_corrections_today`, `record_throttle_rejection`,
  `build_correction_candidate`) split out so the extractor stays
  under the 150-SLOC ceiling.
- `src/agent_memory_lite/ingestion/correction_promotion.py` —
  shared service used by both the HTTP route and the MCP stdio
  handler so promotion semantics are identical across surfaces.
- `src/agent_memory_lite/ingestion/correction_promotion_guards.py` —
  guard helpers (`guard_name_taken`, `coerce_applies_to`,
  `CorrectionPromotionError`) split out so the service module stays
  under the 150-SLOC ceiling. The name-collision guard is what makes
  `overwrite=False` (the default) refuse to silently clobber an
  existing same-name behavior_instruction.
- `src/agent_memory_lite/api/routes/promote_to_behavior.py` +
  `api/schemas/promote_to_behavior.py` — new endpoint
  `POST /memory/promote_candidate_to_behavior`.
- `scripts/transcript_pair_extractor.py` — read-only Claude Code
  JSONL parser; tail-bounded (~400 lines) and best-effort (returns
  `None` on any parse error).
- `scripts/crash_test/phases/p26_v110_correction.py` — full
  end-to-end crash-test phase (claim → correction → candidate →
  promote → envelope check).
- `tests/unit/extraction/test_correction_patterns.py` (25 tests
  including hypothesis property tests).
- `tests/unit/extraction/test_correction_extractor.py` (8 tests).
- `tests/unit/scripts/test_transcript_pair_extractor.py` (11 tests).
- `tests/e2e/test_promote_to_behavior.py` (9 route round-trip tests
  covering happy path, `pinned=true`, `overwrite=true` replacement,
  name-collision 409, wrong-kind 409, and the
  `rule_text_override` length cap).
- `tests/integration/test_correction_loop_e2e.py` (full pipeline +
  flag-off check).
- `tests/integration/test_correction_detector_on_corpus.py` —
  retrospective verification: detector catches the three documented
  corrections from the v1.10 design session.
- `tests/invariants/test_v110_parity.py` — locks flag-off behavior
  byte-equivalent to v1.1.1.
- `tests/unit/mcp/test_correction_via_mcp_local.py::test_mcp_promote_to_behavior_schema_exposes_full_field_set`
  — regression test that asserts the full 13-field set
  (`workspace_id`, `candidate_id`, `name`, `rule_text_override`,
  `rationale`, `kind`, `scope`, `priority`, `conflict_policy`,
  `applies_to`, `decided_by`, `pinned`, `overwrite`) is present in
  the stdio `inputSchema` for `memory_promote_candidate_to_behavior`.
  Caught by a post-release four-AI-agent audit pass.

### Changed

- `src/agent_memory_lite/extraction/thresholds.py` — `CORRECTION`
  threshold lowered from `(0.85, 0.70)` to `(0.5, 0.5)` so heuristic
  matches in the 0.5–0.85 range surface for review. Trust gate
  remains enforced at the promote step.
- `src/agent_memory_lite/ingestion/auto_promote.py` —
  `_build_extractors` now accepts an optional connection so the
  `CorrectionExtractor` can resolve paired claim text.
- `src/agent_memory_lite/retrieval/pending_review.py` — surfaces
  `correction_candidate` queue alongside `decision_candidate` and
  `insight_candidate`, with a hint pointing at the new promote
  endpoint.
- `scripts/inject_memory_context.py` — adds
  `_maybe_capture_correction()` helper; runs best-effort before the
  normal context-injection path.

### Env flags (every default ON; flag-off path locked by parity test)

```
MEMORY_CORRECTION_DETECT_ENABLED=true
MEMORY_CORRECTION_TRANSCRIPT_READ_ENABLED=true
MEMORY_CORRECTION_MIN_USER_LEN=30
MEMORY_CORRECTION_MIN_AGENT_LEN=50
MEMORY_CORRECTION_MIN_CONFIDENCE=0.5
MEMORY_CORRECTION_MAX_PER_DAY=20
MEMORY_CORRECTION_PAIR_WINDOW_MIN=30
```

### Audit-log additions

- `extraction.correction_detected` (when the CorrectionExtractor
  emits a candidate; written by the existing
  `write_memory_candidate` audit path).
- `memory_candidate.promoted_to_behavior` (operator promoted via
  the new endpoint).

### Breaking changes

None. All v1.10 behaviour is gated behind
`MEMORY_CORRECTION_DETECT_ENABLED`. Set to `false` in `.env` to
restore byte-equivalent v1.1.1 behavior; the parity invariant
test enforces this in CI.

### Hardening (post-design audits)

Six rounds of adversarial AI-agent audits found and fixed:
- **SECURITY**: workspace_id check + provenance check
  (`metadata.correction_role="claim"` required) in
  `_resolve_claim` so a forged `correction_target_episode_id` cannot
  leak text from a foreign or unrelated same-workspace episode.
- **DATA-LOSS**: episode dedup now bypasses correction pairs in
  `ingest_episode` — without this, a repeated correction would be
  silently collapsed into the previous episode and the recurring
  mistake would never surface as a second candidate. Locked by
  `tests/integration/test_correction_loop_e2e.py::
  test_correction_pair_bypasses_episode_dedup`.
- **AUDIT**: throttle rejection (`extraction.correction_rejected_throttled`)
  and `overwrite=true` now both land in `audit_log` so operator
  history is complete.
- **ATOMICITY**: promotion writes the durable `behavior_instruction`
  first, then optional pin, then candidate-flip + audit; on partial
  failure the operator-recoverable state is preserved.
- **NAMESPACE**: episode metadata switched to `correction_role` to
  avoid future collisions with other `metadata.kind` users; legacy
  `metadata.kind` still accepted for backward compat.
- **MCP PARITY**: shared `coerce_applies_to` helper used by both HTTP
  and MCP paths so a stringy `applies_to` doesn't split into a
  per-character tuple. New endpoint `memory_promote_candidate_to_behavior`
  registered in MCP stdio + dispatch + tool-registry.
- **NAME COLLISION**: promote refuses to silently replace an active
  rule with the same name unless `overwrite=true` is explicit.
- **SCHEMA**: `rule_text_override` and `rationale` capped at 2000 chars
  so a 100KB injection can't bloat the envelope.
- **PATTERN COVERAGE**: added em-dash + en-dash to Russian opener,
  added first-person fact-evidence opener (`я буквально`,
  `я только что`, `i literally`), tilde expansion for transcript
  paths.
- **LIVE VERIFICATION**: full HTTP loop validated against the running
  service (claim → correction → candidate → promote → behavior_instruction
  → envelope) on both `agentLight` and `copyBot` workspaces.
- **POST-RELEASE AUDIT**: a separate four-AI-agent audit pass on the
  shipped surface caught two real gaps. (1) `stdio_tools_review.py`
  declared `rationale` and `applies_to` in the
  `memory_promote_candidate_to_behavior` `inputSchema` but was missing
  the `overwrite` boolean — the Python-side handler at
  `tools_review.memory_promote_candidate_to_behavior` already read
  it, so MCP stdio clients silently lost the
  name-collision-replace path. Fixed in this same release surface
  with regression test added (see `Added` section).
  (2) `docs/AGENT_CONTRACT.md` JSON example listed only 10 of the 13
  request-body fields (`rationale`, `applies_to`, and `overwrite`
  were undocumented). Updated and re-synced via
  `setup_agent.py --sync-repo` and `--project copyBot` so the
  canonical block is byte-identical across all five contract
  surfaces (`agent-memory-lite/CLAUDE.md`, `AGENTS.md`,
  `copyBot/CLAUDE.md`, `AGENTS.md`, and the operator's global
  `~/.claude/CLAUDE.md`).

### Documentation cross-references

- `README.md` — added a "Latest release: v1.2.0" callout near the
  top pointing at `CHANGELOG.md` and `docs/V1_2_0.md`.
- `CLAUDE.md` (project section, outside the contract block) — added
  a pointer to `docs/V1_2_0.md` from the v1.10 subsection so the
  operator runbook is one click away from the "Memory-quality
  features" map, paralleling the existing `V1_1_0.md` pointer.

## 1.1.1 — 2026-05-04

**Headline:** UI observatory bug fixes + demo carousel hardening. Pure
patch — no behaviour change in retrieval, ingestion, scoring, or
storage. Every flag, every endpoint, every wire format identical
to 1.1.0.

### Fixed

- **UI: orb lit but spokes never drew** during burst writes
  (`src/agent_memory_lite/ui/app.js:1836-1864`). Every `graph_delta`
  event was firing `state.liveLight.set(fid, ...)` immediately,
  lighting the family bubble for 5s. Meanwhile the matching cycle
  was queued behind earlier ones; while the active cycle drew spokes
  for *its* families, the next-queued families' bubbles were
  pre-lit by liveLight without spokes — the user saw a "ghost" orb.
  Fix: skip liveLight when the event has a `request_id` (those
  always produce a cycle that lights the bubble at the correct
  moment via `drawFamilies`). Bare events without a request_id keep
  the pulse as their only feedback signal.
- **UI: "Skills" rendered as a separate node inside Skills family**
  (`src/agent_memory_lite/ui/app.js:1031-1055`). When a `get_context`
  cycle hit multiple capability sub-tables (e.g. `agent_skills` +
  `capability_links`), the `agent_skills` sub-family bubble was
  labeled "Skills" — duplicating the parent family label. Same
  latent issue for `episodes` inside Episodes, `decisions` inside
  Decisions, etc. Fix: `drawSubFamily` now suppresses the sub-family
  label text when it equals the parent family label. The bubble
  itself remains so the structural grouping stays visible; only
  the redundant text is dropped.

### Added

- `scripts/demo_carousel.sh` — 15-step memory churn carousel for
  README video / GIF recording. Hits every action category: search,
  ingest, write_decision, pin, upsert (concept / skill),
  link_capability, archive (decision / episode), accept insight
  candidate, reject decision candidate, update_task_state, explain.
  Runs ~50s, deterministic — steps 12/13 inject fresh pending
  candidates so the demo always exercises the accept/reject paths.
- `docs/demo.gif` — live observatory demo embedded at the top of
  README.md.
- `docs/OPERATIONS.md` — operator runbook covering upgrade workflow,
  service auto-start (Task Scheduler vs Startup folder vs manual),
  hook fallback chain, hub-mode + legacy-DB behaviour, common
  failure modes.

## 1.1.0 — 2026-05-04

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

### Hardening (post-ship operational fixes folded into 1.1.0)

The 1.1.0 ship surfaced three operational gaps observed during real
deployment. All three landed in this release:

* **MCP function-call markup guard.** When an agent invoked a
  `memory_*` MCP tool with its own function-call boundary tags
  (`</decision_text>`, `<parameter name="...">`, `</invoke>`)
  embedded in the textual content of a parameter, that markup got
  persisted verbatim. New `redaction/mcp_markup.py:strip_mcp_markup`
  truncates at the first marker; idempotent on clean input.
  Pydantic `SafeText` / `SafeTextOptional` annotations apply the
  strip via `AfterValidator` to every text field on every write
  surface (`decisions`, `episodes`, `theories`,
  `behavior_instructions`, `concepts`, `insights`, `roles` /
  `skills` / `playbooks`). Strict markers only — generic angle
  brackets pass through untouched. One-shot cleanup tool
  `scripts/repair_text_artifacts.py` walks every text column and
  applies the same strip + recovers the leaked rationale block.

* **UserPromptSubmit hook FTS fallback.** Pre-hardening the
  auto-injection hook was HTTP-only: if the service at
  `127.0.0.1:8765` was down, every prompt got an empty notice and
  the agent ran blind. New `scripts/inject_memory_fts_fallback.py`
  opens SQLite directly, runs FTS on `chunks_fts` plus
  structured-section reads from `core_memory` /
  `behavior_instructions` / `decisions`, renders a minimal envelope
  in ~30ms (no embedding load — would be 2-3s cold start, unaccept-
  able per-prompt). Hook now degrades HTTP → FTS → notice instead
  of HTTP → notice. MCP stdio already had a similar fallback; the
  two surfaces are now symmetric.

* **Hub-mode dispatch on legacy-schema DBs.** The HTTP service in
  hub mode routes per-call via `X-Memory-DB-Path` header. If the
  hook's cwd doesn't match any registered project root, it
  auto-bootstraps a global workspace at `~/.agent_memory/global/` —
  which may have only v1.0.x migrations applied. Three v1.5 / v1.6 /
  v2.2 post-build hooks now catch `sqlite3.OperationalError` and
  degrade to a no-op when the column / table is missing
  (`pending_review.load_pending_review`,
  `last_retrieved_tracker._update_kind`,
  `behavior_apply.mark_behavior_instructions_applied`). Hub mode
  serves correctly on legacy DBs — features that depend on new
  schema simply skip, which is the correct semantics.

### Quality gates

* `pytest -q` — 655 passed (was 491 in 1.0.3 baseline; +164).
* `ruff check` + `ruff format --check` — clean across 664 files.
* `mypy src` — strict, 0 issues across 432 source files.
* `python scripts/check_sloc.py --enforce` — every `src/**/*.py` ≤ 150 SLOC.
* Crash test (`scripts/crash_test`, 26 phases / 122 assertions) — PASS.

### Operations

`docs/OPERATIONS.md` (new) — operator runbook covering upgrade
workflow (restart MCP server / HTTP service / verify migrations),
service auto-start options (Task Scheduler vs Startup folder vs
manual), hook fallback chain, hub-mode + legacy-DB behaviour,
troubleshooting common failure modes, workspace lifecycle.

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
