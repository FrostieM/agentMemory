# SESSION_STATE.md — agent-memory-lite

Rolling state for cross-session work. Pair-read with `CLAUDE.md`.

## Current state — 3.7.1 (plan-storage redesign in progress + cross-workspace leak fix)

Work since 3.0.0 runs on the 3.x line — the v3.1–v3.7 feature cycles
shipped (per-version history in `CHANGELOG.md`). The active focus is
the **plan-storage redesign**, with a set of audit-/incident-driven
fixes carried alongside it.

### Plan-storage redesign — replacing the flat plan model

Decision `dec_59ddc0bb896c4f7e`: replace the flat plan model (two
JSON string lists `current_plan` / `completed_steps` upserted in
place on the `task_state` row) with first-class `plan_steps` rows —
id, task_id, parent_step_id, rank, title, body, status
(pending/active/done/blocked/skipped), supersedes_step_id,
source_episode_id, bi-temporal `valid_from` / `valid_to`. Six phases:

- **Phase 1 — data model + migration.** Done — `plan_steps` table
  (migration 0038) + model hardening. `77d15f7`, `c5aa202`.
- **Phase 2 — plan writes + history.** Done — versioned write engine
  via `writer.py`; `valid_to` timestamp alignment. `b63d9cc`,
  `f2edc37`.
- **Plan-step MCP surface.** Done — `plan_step` added to the generic
  tool kind enums (`memory_get` / `memory_search` / …). `29ca4b8`
  (task #111).
- **Phase 3 — pinned compact plan view in the brief.** Done — the
  active task's plan rides every brief, rendered compact (goal +
  N/M done + in-progress step + next steps); full detail stays
  fetch-on-demand. `8223eb5`.
- **Phase 4 — skill activation on step-enter.** Done — capabilities
  (skills / roles / playbooks) linked to the in-progress step via
  `capability_links` surface as an `apply:` brief line. `89d248c`.
- **Phase 5 — smart plan layer** (Large — subdivided):
  - **5a — plan-health review nudges.** Done — heuristic nudges
    (more than one step active, no active step, blocked work)
    render as a `review:` brief line. `2f9cc9b`.
  - **5b — distill a completed plan into a playbook.** Pending
    (task #113) — when every live step is done/skipped, create a
    playbook capability from the step titles in rank order.
  - **5c — step outcomes feed skill maturity.** Pending (task #114)
    — on a step status change, bump bound capabilities' maturity
    counters (done → success; skipped/blocked → failure).
- **Phase 6 — plan UI in the observatory.** Pending (task #110).

Every completed phase passed round-based adversarial AI audits.
**Next step: Phase 5b** (task #113) — the playbook distiller.

### Fixes of various types (carried alongside the redesign)

- **Cross-workspace ingest-leak guard — `b71cc62`, shipped as
  3.7.1.** In hub mode a `POST /memory/ingest_file` /
  `/memory/ingest_episode` naming a foreign `workspace_id` could
  land in the service's anchor DB when `WorkspaceRoutingMiddleware`
  routing was bypassed (a stale service, or a wrong `X-Memory-DB-Path`
  header). On 2026-05-21, 134 copyBot `ingest_file` calls leaked
  11,334 rows into the agent-memory-lite DB. `ensure_workspace_writable`
  could not catch it — it permits every workspace in hub mode and
  every foreign workspace with strict isolation off. New guard
  `ensure_workspace_matches_db` (`api/workspace_routing.py`) compares
  the connection's physical file (`PRAGMA database_list`) against the
  workspace's registered DB (`os.path.samefile`, with a
  `Path.resolve()` fallback) and raises `ValidationError` before any
  row is written on a mismatch. 3 adversarial audit rounds, 13 paired
  tests. The 11,334 leaked rows were quarantined out of the
  agent-memory-lite DB (`scripts/memory_workspace_doctor.py
  --quarantine --backup-first`; full-DB backup + JSON row-export
  under `.agent_memory/backups/`). **Follow-up task #115:** extend
  the guard to the remaining ~28 write routes.
- **Brief-cache per-table fingerprint — `6fe7a8f`.**
  `_workspace_fingerprint` rewritten to hash per-table `MAX`
  timestamps (decisions / behaviors / tasks / code_digests /
  episodes / capability_links / plan_steps) instead of a single
  global `MAX`, so a write to one table is never masked behind a
  higher timestamp in another — including the mixed `Z` / `+00:00`
  timestamp formats across writers.
- **Pre-commit ingest-hook retry — `2cd3de4`.** The dogfooding
  pre-commit hook (`scripts/git_hooks/pre-commit`) now retries, with
  a deadline bound, any staged file that failed the parallel
  `/memory/ingest_file` batch, so a transient indexing failure no
  longer leaves files unindexed.

### Operator note (3.7.1)

Restart the HTTP service so it runs the new code (`/health` must
report `version=3.7.1`) — the cross-workspace guard is inert until
the running service is restarted. Restarting Claude Desktop / Cursor
is optional for this fix: it is HTTP-side; the MCP stdio path routes
per-call and was never part of the leak.

## Previous state — 3.0.0 (memory as a brain + agent UX follow-ups)

**3.0.0 ships 2026-05-19 as the v3 final release** with the
agent-UX follow-ups consolidated on top of the same version line
(pyproject.toml + version.py finally bumped from 2.0.0 → 3.0.0 to
match the existing ``v3.0.0`` git tag). Every retrievable row
carries `outcome_score`; co-retrievals form Hebbian `soft_edges`
(with HeLa-Mem validation gate); sleep consolidation distills
`insights` → recurring ones promote to pinned behaviors; PreToolUse
`reflex_rules` can block tool calls on missing preconditions
(advisory by default, operator promotes to block); per-workspace
`self_model` surfaces identity narrative FIRST in every brief;
bi-temporal `valid_from/valid_to` filtering keeps superseded
knowledge from active view; `memory_recall(topic, depth,
outcome_floor)` spreads activation over `soft_edges ∪
capability_links ∪ causal_links`.

**Agent UX follow-ups (2026-05-19):**

- MCP routing bug — every v3-strict AND legacy handler now routes
  through ``_runtime.db_for(workspace_id)`` instead of bare
  ``_runtime.db()`` so a misconfigured anchor stops silently
  targeting the wrong DB. Warn-once log fires on registry-miss
  fallback. 40+ handler sites fixed across ``stdio_handlers_*.py``.
- Move 5 — ``decision_neighbors`` field on every
  ``write_decision`` / ``record_with_evidence`` response (top-3
  active decisions with token overlap on the new write, self-
  excluded). Read-only hint so the agent supersedes instead of
  fragmenting.
- Sticky-brief — ``compose_brief`` accepts ``session_id``; first
  call renders at the requested budget, subsequent calls in the
  same session shrink to
  ``MEMORY_STICKY_BRIEF_FOLLOWUP_TOKENS`` (default 200).
- Aging-decisions brief section — active decisions older than
  ``MEMORY_AGING_DECISIONS_DAYS`` (default 30) with
  ``outcome_score == 0.0`` surface as a "still active?" hint.
- Deprecation-notice dedup — the v2-compat shim emits each
  ``deprecation_notice`` once per process; suppressible globally
  via ``MEMORY_SUPPRESS_DEPRECATION_NOTICES=true``.

Plus a brain-aware dashboard: `/ui` (Observatory + self-model card +
watch-outs + recent-insights), `/ui/recall`, `/ui/reflexes`,
`/ui/metrics`. All shipping as one-shot setup via
`scripts/setup_agent.py --project /path` (applies 7 brain migrations
0002-0008, seeds 3 discipline behaviors + 3 baseline reflex rules,
wires hooks, registers workspace).

Phase 7 completes the v3 plan. Next session — start v3.1 work using
[`docs/V3_1_BREAKTHROUGH_ROADMAP.md`](docs/V3_1_BREAKTHROUGH_ROADMAP.md)
(6 vectors: experiment proposal, adaptive retrieval, blindspot
detection, learned causality, predictive failure detection, inter-
agent negotiation).

## Historical state — 2.0.0 (first public release, consolidated baseline)

**2.0.0 ships 2026-05-10 as the first public-facing release.** It
consolidates ~6 months of internal incremental development (the 1.x
→ 2.1.x → 2.2.x lineage, archived in
[`docs/CHANGELOG_LEGACY.md`](docs/CHANGELOG_LEGACY.md)) into a
single coherent baseline. Every memory feature is on by default;
flag-off parity invariants in `tests/invariants/` lock the legacy
byte-equivalent path for any operator who wants to peel a layer
off.

The four adoption-by-default Moves (auto-thread `source_episode_id`,
compound `record_with_evidence`, `capability_suggestions` on
decision and theory writes) are the headline feature: they make the
discipline rules server-defaulted instead of agent-remembered. The
v1.6 telemetry that motivated them showed
``decision_provenance=0.00`` on `agentLight` (0/13 decisions had
``source_episode_id``); after Move 1 ships every fresh write
auto-fills the field.

The four Moves work identically across the HTTP route, the MCP
stdio handler, and the in-process MCP tool — local-fallback
deployments keep the full surface even when the HTTP service is
unreachable. The shared chokepoint is
``ingestion/_write_helpers.py``
(``resolve_source_episode_id`` + ``capability_suggestion_dicts``).

## Previous state — 2.2.0 (incremental adoption series before consolidation)

Operator complaint: v1.6 adoption telemetry on ``agentLight`` showed
``decision_provenance=0.00`` (0/13 decisions had ``source_episode_id``),
``link_after_write=0.31`` amber, ``candidate_triage=0.30`` amber. The
agent kept skipping the rules. The right fix is to make the rules
unforgettable — the server does the discipline work the agent
reliably forgets. v2.2 is that work.

**Four small server-side moves**, each behind a default-on flag:

1. **Move 1** — auto-thread ``source_episode_id`` on
   ``write_decision`` / ``write_theory`` from the agent's most
   recent ``ingest_episode`` (10-min window). ``allow_orphan: true``
   opts out. Off-switch: ``MEMORY_AUTOTHREAD_DECISION_SOURCE=false``.
2. **Move 2** — compound write tool ``memory_record_with_evidence``:
   atomic ``ingest_episode + write_decision + optional
   link_capability``. Skip the 3-step ritual.
3. **Move 3** — ``capability_suggestions`` field on
   ``write_decision`` and ``record_with_evidence`` responses. Top-3
   workspace capabilities ranked by token-overlap with decision
   text. Read-only hint, never auto-links.
4. **Move 4** — ``capability_suggestions`` extended to
   ``write_theory`` response too. Same shape, same contract.

**MCP regression fix.** All four Moves were initially wired only on
the HTTP route; the MCP stdio handler's local-fallback branch (used
when the HTTP service is down) and the in-process MCP tool handlers
returned bare 4-field dicts without Move 1 / 3 / 4 fields. Extracted
shared ``ingestion/_write_helpers.py`` (``resolve_source_episode_id``
+ ``capability_suggestion_dicts``) and aligned all four entry points
(HTTP route + stdio handler + in-process tool, both for decisions
and theories).

**SLOC polish (debt reduction).** All three Move-related route files
trimmed back below 150 SLOC by the same helper extraction:
``decisions.py`` 153 → ~140, ``theories.py`` 156 → ~137,
``record_compound.py`` 164 → ~143. Only ``config/settings.py``
remains grandfathered (separate composed-Settings decomposition).

**Quality gates green:**
- ``ruff check`` ✓
- ``ruff format --check`` ✓
- ``mypy`` ✓
- ``check_sloc.py --enforce`` ✓
- **964 tests pass** (was 882 in 2.1.5; 82 paired tests added).

**Files added (5 source + 4 test):**
- ``src/agent_memory_lite/ingestion/auto_thread_provenance.py``
- ``src/agent_memory_lite/ingestion/capability_suggester.py``
- ``src/agent_memory_lite/ingestion/_capability_suggester_stopwords.py``
- ``src/agent_memory_lite/ingestion/_write_helpers.py``
- ``src/agent_memory_lite/api/routes/_capability_suggest_payload.py``
- ``src/agent_memory_lite/api/routes/_record_compound_link.py``
- ``src/agent_memory_lite/api/routes/record_compound.py``
- ``src/agent_memory_lite/api/schemas/record_compound.py``
- ``src/agent_memory_lite/mcp/tools_compound.py``
- ``src/agent_memory_lite/mcp/stdio_tools_compound.py``
- ``tests/unit/ingestion/test_auto_thread_provenance.py``
- ``tests/unit/ingestion/test_capability_suggester.py``
- ``tests/unit/ingestion/test_write_helpers.py``
- ``tests/unit/mcp/test_write_local_fallback.py``

**Files modified (substantive):**
- ``src/agent_memory_lite/api/routes/decisions.py`` — Move 1+3 wiring +
  helper extraction.
- ``src/agent_memory_lite/api/routes/theories.py`` — Move 1+4 wiring +
  helper extraction.
- ``src/agent_memory_lite/api/routes/theory_responses.py`` —
  ``theory_in_from_body`` / ``evidence_in_from_body`` builders.
- ``src/agent_memory_lite/api/schemas/decisions.py`` — Move 3 payload
  (``CapabilitySuggestionPayload``, ``allow_orphan``).
- ``src/agent_memory_lite/api/schemas/theories.py`` — Move 4 payload
  + ``allow_orphan``.
- ``src/agent_memory_lite/config/settings.py`` —
  ``auto_thread_decision_source`` flag.
- ``src/agent_memory_lite/mcp/stdio_handlers_decisions.py``,
  ``stdio_handlers_theories.py``, ``tools_decisions.py``,
  ``tools_theories.py`` — local-fallback alignment.
- ``CHANGELOG.md``, ``SESSION_STATE.md``, ``docs/MEMORY_API.md``,
  ``docs/AGENT_CONTRACT.md``, ``docs/AGENT_CHEATSHEET.md`` — Move 1–4
  wire docs + agent operating contract updates.
- ``CLAUDE.md``, ``AGENTS.md`` — re-synced via
  ``scripts/setup_agent.py --sync-repo``.
- ``pyproject.toml``, ``src/agent_memory_lite/version.py`` — 2.2.0.

**Operator note.** After ``git pull``, restart Claude Desktop /
Cursor / VS Code (MCP stdio servers don't auto-reload) AND restart
the HTTP service (``python -m agent_memory_lite``) so the wire
schemas pick up ``capability_suggestions`` on the theory response.
Verify with ``curl http://127.0.0.1:8765/health`` →
``version=2.2.0``.

## Previous state — 1.2.0 (v1.10 correction-aware learning loop)

Closes the **structurally missing loop** observed in 1.1.1: the
agent saw user corrections, fixed the immediate thing, then forgot
the lesson. v1.10 makes the loop automatic.

**Three-stage pipeline** (all behind `MEMORY_CORRECTION_DETECT_ENABLED`,
default true):

1. **Capture** — `inject_memory_context.py` reads the Claude Code
   transcript JSONL (`event.transcript_path`), finds the most recent
   assistant text turn within 30 minutes. If the current user prompt
   matches the correction heuristic, ingests both turns as paired
   episodes via `/memory/ingest_episode` with cross-reference
   `metadata.correction_target_episode_id`.
2. **Extract** — new `CorrectionExtractor` (registered alongside
   `HeuristicExtractor` and the Ollama extractor) emits a
   `MemoryCandidate(kind=CORRECTION)` with 0.5 / 0.7 / 0.85
   confidence based on opener / body / both-match. Surfaces in
   `<pending_review>` envelope.
3. **Promote** — operator-driven `POST
   /memory/promote_candidate_to_behavior` writes a
   `behavior_instruction` with `source_type="memory_candidate"`,
   `source_id=<candidate.id>` for lineage. Trust gate stays intact;
   no auto-promote.

**Validation:** `tests/integration/test_correction_detector_on_corpus.py`
locks the three documented corrections from the v1.10 design session
as the retrospective verification corpus. All three match in the
0.5–0.85 confidence range.

**Tests added:** 25 pattern unit tests (incl. 4 hypothesis
properties), 12 extractor tests (incl. throttle, workspace
isolation, cross-workspace), 11 transcript-extractor tests, 9
promote-route e2e tests (incl. pinned + overwrite + name-collision +
wrong-kind 409 + length cap), 2 full-loop integration tests, 3
retrospective verification tests, 3 parity invariant tests, 4 MCP
wire-up tests, 1 live-transcript test = **70 new tests**.
Crash-test grows to 27 phases with `p26_v110_correction`.

**Files added (8 source + 7 test):**
- `src/agent_memory_lite/extraction/correction_patterns.py`
- `src/agent_memory_lite/extraction/correction_extractor.py`
- `src/agent_memory_lite/extraction/correction_distill.py`
- `src/agent_memory_lite/ingestion/correction_promotion.py`
- `src/agent_memory_lite/ingestion/correction_promotion_guards.py`
- `src/agent_memory_lite/api/routes/promote_to_behavior.py`
- `src/agent_memory_lite/api/schemas/promote_to_behavior.py`
- `scripts/transcript_pair_extractor.py`
- `scripts/crash_test/phases/p26_v110_correction.py`
- `tests/unit/extraction/test_correction_patterns.py`
- `tests/unit/extraction/test_correction_extractor.py`
- `tests/unit/scripts/test_transcript_pair_extractor.py`
- `tests/e2e/test_promote_to_behavior.py`
- `tests/integration/test_correction_loop_e2e.py`
- `tests/integration/test_correction_detector_on_corpus.py`
- `tests/invariants/test_v110_parity.py`

**Files modified:**
- `src/agent_memory_lite/config/settings.py` — 7 env flags
- `src/agent_memory_lite/extraction/thresholds.py` — CORRECTION
  threshold lowered to (0.5, 0.5)
- `src/agent_memory_lite/ingestion/auto_promote.py` — register
  CorrectionExtractor when flag on + conn passed
- `src/agent_memory_lite/retrieval/pending_review.py` — surface
  correction_candidate queue
- `src/agent_memory_lite/api/app.py` — wire new router
- `scripts/inject_memory_context.py` — `_maybe_capture_correction`
- `scripts/crash_test/runner.py` — register `P26V110Correction`
- `.env.example` — document new flags
- `CHANGELOG.md`, `SESSION_STATE.md`, `pyproject.toml`,
  `version.py` — 1.2.0 release entries

## Previous state — 1.1.1 (UI bug fixes + demo carousel)

Two UI observatory bugs caught during the README video recording:

1. `state.liveLight.set(...)` was firing for every `graph_delta`
   regardless of whether a cycle would later draw spokes for that
   family — burst writes produced "orbs lit but no spokes" while
   the cycle queued behind earlier ones.
   Fix: skip liveLight when the event has a `request_id` (cycle
   handles the lighting at the correct moment).
2. Sub-family bubble for `agent_skills` rendered with label
   "Skills" inside the parent Skills family bubble (also
   `episodes`, `decisions`, `theories` had the same latent
   conflict).
   Fix: `drawSubFamily` suppresses the label text when it equals
   the parent family label.

Plus `scripts/demo_carousel.sh` — 50-second 15-step churn for
README video; deterministic via injected pending candidates for
steps 12/13. `docs/demo.gif` embedded at top of README.

No retrieval / scoring / ingestion / storage changes. All
v1.4-v1.9 + v2.1-v2.3 behaviour identical to 1.1.0.

## Previous state — 1.1.0 (defaults ON, calibrated, hardened for hub-mode + MCP-markup-safe)

Six evolution loops (v1.4 feedback-aware scoring through v1.9 hygiene
recurrence) are merged on `main`. As of the A+ calibration the flags
**default ON** so a fresh checkout opts in out of the box; explicit
`false` in `.env` reverts to v1.0.x baseline (locked by
`tests/invariants/test_v2_parity.py`).
Three v2 improvements (97d30fa) close known gaps: implicit feedback
derives feedback rows from operator actions, `<pending_review>` surfaces
candidates inside the context envelope, and the sentinel scheduler
auto-runs on traffic. Hub-mode routing for v2.3 fixed in this release —
the scheduler now uses the request-scoped DB path resolved from the
connection rather than the singleton `settings.db_path`.

The v2 implementation is **calibrated against real copyBot data** in
`reports/v1_1_0_calibration/`. Replaying 1370 audit rows produced
158 implicit feedback rows; the resulting EWMA term shifted ranking in
the right direction (95% rank churn, low-EWMA cohort dropped 26 places,
high-EWMA cohort rose 0.84 places, biggest faller -51 positions for a
high-importance decision with zero operator interaction).

Everything below describes the stable 1.0.3 base; the v1.4-v1.9 + v2
loops are env-flag-gated additions, not replacements.

### v1.4-v1.9 loops + v2 improvements (env-flag map; defaults ON)

* **v1.4** `MEMORY_FEEDBACK_EWMA_ENABLED=true` — completes the scoring
  formula with a feedback-EWMA term (decisions/theories/chunks).
* **v1.5** `MEMORY_CAPABILITY_MATURITY_ENABLED=true`,
  `MEMORY_BEHAVIOR_APPLY_TRACKING_ENABLED=true` — usage/success counters
  on roles/skills/playbooks; behavior application count.
* **v1.6** `MEMORY_COLD_TRACKING_ENABLED=true`,
  `MEMORY_COLD_AUTO_QUEUE_ENABLED=true` — `last_retrieved_at` stamping +
  cold-candidate emission.
* **v1.7** `MEMORY_THEORY_BRIDGE_ENABLED=true`,
  `MEMORY_THEORY_BRIDGE_MIN_EVIDENCE=3` — validated-theory →
  decision_candidate bridge (never auto-promotes; review-only).
* **v1.8** `MEMORY_REFLECTIVE_COMPACT_ENABLED=true`,
  `MEMORY_LESSON_MIN_SUPPORT_EPISODES=4`, `MEMORY_LESSON_MAX_PER_RUN=10` —
  Ollama-driven lesson extraction → insight_candidates (review-only;
  gracefully degrades when Ollama unreachable).
* **v1.9** `MEMORY_HYGIENE_PERSIST_ENABLED=true`,
  `MEMORY_SENTINEL_PERSIST_ENABLED=true`, `MEMORY_RECURRENCE_THRESHOLD=3` —
  hygiene findings/sentinel runs persisted with recurrence counters.
* **v2.1** `MEMORY_IMPLICIT_FEEDBACK_ENABLED=true` — derives feedback
  rows from archive/promote/link actions (archive=-1.0, promote=+0.7,
  link=strength).
* **v2.2** Pending review envelope — auto-injected when
  `decision_candidates` or `insight_candidates` rows exist (no flag;
  data-driven).
* **v2.3** `MEMORY_SENTINEL_AUTORUN_HOURS=6.0` — daemon-thread scheduler
  triggers sentinel runs on get_context traffic when overdue. Per-workspace
  in-flight lock (`sentinel_lock`) prevents duplicate concurrent daemons.
  Hub-mode aware: routes via PRAGMA-resolved DB path.

Calibration evidence and A/B numbers in `reports/v1_1_0_calibration/report.md`.
Flag-off byte-equivalence locked by `tests/invariants/test_v2_parity.py`.
End-to-end coverage by crash phase `p25_v2_improvements.py`.

### Quality gates after 1.1.0 ship + defaults flip (current)

* `pytest -q` — **632 tests passing** (was 491 in 1.0.3 baseline; +6 v2 unit,
  +4 property, +3 invariant, +4 hub-mode, +4 v2.2 actionable, +rest from v1.4-v1.9).
* `ruff check / format --check` — clean across 657 files.
* `mypy src` — strict, clean across 430 source files.
* `python scripts/check_sloc.py --enforce` — every `src/**/*.py` ≤ 150 SLOC.
* Crash test (modular, 26 phases / 120 assertions): **PASS** (1 phase skip when Ollama unreachable).

### What 1.0.3 added on top of 1.0.2

- **Idempotent agent-contract sync.** `scripts/setup_agent.py:upsert_contract`
  is now byte-stable across reruns: `render_contract_block()` produces the
  same canonical block whether the file is being created or updated, and the
  end-marker search uses `rfind` so the replaced span runs from the FIRST
  `:begin` to the LAST `:end`. A hand-broken anchor file with stray
  duplicate `:end` markers is healed in a single sync pass instead of
  silently accumulating drift. Confirmed by paired property-style tests in
  `tests/unit/scripts/test_setup_agent_upsert_contract.py` (created /
  idempotent / preserves user content / heals duplicate end / dangling
  begin / no-marker append).
- **Stable anchor-file format.** `CLAUDE.md` and `AGENTS.md` were resynced
  once with the new format (single trailing newline before / after the block,
  no leading newline drift). Subsequent CI runs report `unchanged`.

### What 1.0.2 added on top of 1.0.1

- **Single-source agent contract.** `docs/AGENT_CONTRACT.md` is now the
  canonical body for the agent operating contract. `CLAUDE.md` and
  `AGENTS.md` carry the same body verbatim between
  `<!-- agent-memory-lite-contract:begin/end -->` markers. After
  editing the canonical file, run
  `python scripts/setup_agent.py --sync-repo` to re-inject into both
  anchor files. Idempotent — second run reports `unchanged`.
- **CI guard against contract drift.** `.github/workflows/ci.yml`
  runs the same sync and `git diff --exit-code -- CLAUDE.md AGENTS.md`,
  so any direct edit to the marker block in the anchor files (without
  syncing the canonical) fails CI. Eliminates silent drift between
  the three files.
- **AGENTS.md brought to v1.0.1 parity.** The previous tagged 1.0.1
  AGENTS.md was missing endpoints that already shipped in CLAUDE.md
  (snapshot_save / list / diff, review_queue, compact_trigger,
  get_object, explain_context). Sync recovered the gap.

### What 1.0.1 added on top of 1.0.0

- **UI live-write refresh fix** — open Decisions / Theories / etc. inspector
  no longer goes stale when a new row is written. The graph_delta handler
  invalidates the per-family detail cache and re-fetches if the inspector
  is currently open on that family, so new rows appear within ~3 s without
  a page reload.
- **Action-colored spokes** — the spoke + object node tint now encodes the
  action: created / upserted / restored = green (150°), pinned = 90°,
  unpinned = 50° amber, archived / superseded = 25° red-orange,
  deleted / rejected = 15° red, reads keep the family hue (neutral). The
  legend at `/ui` was extended to 8 rows showing the same oklch hues the
  painter uses so the swatch and the graph match.
- **README + SESSION_STATE patch** — diagram step ② / ③ now lists every
  read / write endpoint (was a 4-tool teaser before), the Episodes / tasks
  access pattern is documented (episodes surface inside `<retrieved_chunks>`
  via the `sources="ep_xxx"` attribute, `<task_state>` only renders when
  `task_id` is in the request), `<index>` discover-then-fetch pattern is
  called out, and the UserPromptSubmit hook auto-fallback to the shared
  `~/.agent_memory/global/` workspace is mentioned in the diagram.

## Quality gates

All green on Python 3.13 / 3.14 (Windows / macOS / Linux):

- `pytest -q` — **491 tests** (unit / property / integration / e2e).
- `ruff check src tests scripts` — clean.
- `ruff format --check src tests scripts` — clean.
- `mypy src` — strict, clean across 394 source files.
- `python scripts/check_sloc.py --enforce` — every `src/**/*.py` ≤ 150 SLOC.
- `python scripts/run_evals.py --workspace <ws> --no-vector` — eval cases pass
  with the recall / precision / stale-fact / leak targets from the spec.
- `python -m scripts.crash_test --skip-llm` against a fresh `qa-crash`
  workspace — modular crash test (~133 assertions) verifying retrieval,
  search, context envelope, cross-references, pin/archive semantics,
  capability links, audit trail, hygiene queue, snapshots, and
  relationship integrity end-to-end. (The monolithic
  `crash_test_v3.py` was retired during the v3 cutover; the modular
  ``scripts/crash_test/`` package supersedes it.)

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
