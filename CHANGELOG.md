# Changelog

All notable changes to **agent-memory-lite**. This file starts at the
2.0.0 baseline; the incremental 1.x → 2.1.x → 2.2.x development
history that produced 2.0.0 is preserved verbatim in
[`docs/CHANGELOG_LEGACY.md`](docs/CHANGELOG_LEGACY.md).

Versioning follows semver from 2.0.0 onward. Minor bumps add
functionality (and may flip a default), patch bumps fix bugs without
behavioural change.

## 3.7.0 — 2026-05-21 (audit re-audit hardening + orphan-vector self-cleaning)

A deep adversarial re-audit of the API/MCP and local-only-security
sectors closed the holes the 3.6.0 sweep's first pass missed, and a new
brain-pass loop makes the vector store self-maintaining.

### Added

- **`prune-orphan-vectors` brain loop (8th brain-pass step).** SQLite
  (chunks) and LanceDB (vectors) have no cross-store transaction, so a
  chunk delete or an interrupted compound write can leave a vector with
  no backing chunk — an orphan that wastes a top-K search slot on a
  dead hit. Every brain pass now diffs the vector ids against the chunk
  ids and deletes the surplus, capped per pass. Flags
  `MEMORY_VECTOR_PRUNE_ENABLED` (default ON) and
  `MEMORY_VECTOR_PRUNE_MAX_PER_PASS` (default 2000); the count surfaces
  on `BrainPassReport.vectors_pruned`.

### Fixed — security re-audit (API / MCP + local-only boundary)

- **The MCP stdio server ran no local-only guard.** `python -m
  agent_memory_lite.mcp.stdio_server` never called `assert_local_only`,
  so a cloud `EMBEDDING_BASE_URL` / `HF_ENDPOINT` / proxy would ship
  data off-machine on the MCP path. `_run()` now runs the guard at
  startup, mirroring the HTTP `create_app`.
- **Cross-workspace write holes.** The v2-compat shim and several MCP
  write handlers (promote / reject candidate, resolve / claim / dismiss
  maintenance event, compact_trigger, propose_experiments persist)
  routed to a caller-supplied workspace with no isolation guard. All now
  guard with write intent before touching a DB; the 5 canonical HTTP
  write routes regained `ensure_workspace_writable`.
- **Local-only guard widened.** It now audits `HF_ENDPOINT` /
  `HF_HUB_ENDPOINT` / `OLLAMA_HOST` / `MEMORY_HTTP_BASE_URL` and the
  `HTTP(S)_PROXY` env vars (a proxy is a transport-level redirect the
  URL checks could not see); every httpx call pins `trust_env=False`;
  the cloud denylist gained more inference / vector hosts.

### Fixed — memory quality

- **Consolidation no longer regenerates word-frequency noise insights.**
  When the LLM consolidation path is unavailable, the heuristic fallback
  produces a "Recurring theme (N episodes): tok, tok" token bag; that is
  now skipped at creation instead of piling a fresh noise candidate into
  the review queue every pass.
- The whole `tests/e2e` suite — red since the v3.5 OriginGuard landed
  (every `TestClient` sent `Host: testserver`) — is green again.

This is a security-hardening + memory-quality release; it is not a
"production-ready" claim — see the adversarial-audit bar in the
contract.

## 3.6.0 — 2026-05-20 (full project audit by AI agents — 8 sectors, 28 fixes, 7 security holes closed)

A round-robin audit by 23 parallel AI agent runs swept the entire
project in 8 sectors (Storage, Retrieval, Ingestion, API/MCP,
Cognition, UI, Scripts, Tests). 28 critical/high findings landed
as fixes in 6 commits over this release, with 60+ new regression
tests locking the contracts.

### Security (7 holes closed)

- **Path-traversal in ``X-Memory-DB-Path`` / ``-Vector-Path``
  headers** (sector 4) — previously any local process / DNS-rebind
  attacker could redirect the service at ANY readable SQLite file
  on disk. ``api/deps.py`` now validates the override against the
  registered workspaces allow-list.
- **Origin / Host guard middleware** (sectors 6-7) — refuses
  browser POSTs from non-loopback Origin and DNS-rebind Host
  headers. Local browsers visiting a malicious page could
  previously trigger ``/memory/promote_candidate`` etc.
- **AGENT_MEMORY_BASE loopback validation** (sectors 6-7) — the
  inject hooks now refuse non-loopback URLs so the env var cannot
  exfiltrate every prompt + brief to an attacker host.
- **XSS in ``ui/graph.html`` + ``ui/code.html``** (sector 6) —
  tooltip + table interpolated raw DB-sourced strings into
  innerHTML.
- **Secret leakage via file_pipeline + capability_writer +
  promote_insight_to_behavior** (sectors 1+3) — three write paths
  bypassed ``redact()``. Capabilities ride every brief; insights
  promoted to pinned behaviors stayed in every envelope. All now
  redact before persist.
- **Trust gate widened** (sector 3) — UNTRUSTED_DOC →
  decision/correction/bug/fix candidates no longer bypass.
- **4 new secret patterns**: Stripe live/test keys, basic-auth
  URLs, GCP service-account marker, generic Bearer tokens.
- **SQL injection allow-list** on ``date_range_clause`` column
  parameter (sector 1).
- **LanceDB workspace_id + vector_id boundary validation** (sector
  2) — DataFusion filter built via f-string was injection-shaped.

### Reliability (silent crashes closed)

- **Universal ``coerce_enum``** swept 8 enum-bearing parsers
  (sectors 1+2) — one bad row in DB can no longer 500 the read
  path (same shape as the v3.4 incident that bricked copyBot for
  2.5h).
- **chunks_repo tolerant JSON + float** — corrupt metadata no
  longer crashes every chunk read.
- **FTS query try/except** — malformed BM25 syntax no longer
  escapes to 500.
- **candidates_vector wrap** — embedder OOM / LanceDB corruption
  now degrade to FTS-only.
- **Global Exception handler** (sector 4) — any unhandled
  exception becomes a typed JSON 500 envelope.
- **brain_pass step isolation** (sector 5) — broadened from
  ``(sqlite3.Error, ImportError)`` to ``Exception`` so one bad
  loop cannot kill the 16 siblings.
- **integrity_audit per-check try/except** (sector 5) — one bad
  check no longer aborts the whole audit.
- **drift_sentinel reopens resolved findings** instead of
  INSERTing duplicates (sector 5).

### Quality

- **FTS OR→AND** join (sector 2) — multi-token queries no longer
  devolve to "any-token match"; main contributor to the NDCG=0.67
  plateau on BEIR SciFact.
- **LanceDB metric=cosine** (sector 2) — was defaulting to L2,
  scores were polluted.
- **Spreading activation hops_seen check** — no exponential
  blow-up on cycles.
- **MRR stays at 0.9889** on MemBench through all 6 commits, no
  regression.

### Data correctness

- **outcome_recompute NULL handling** (sector 5) — distinguishes
  NULL from 0.0; rows no longer stay NULL forever.
- **theory_evidence strength_delta** missing
  AUTONOMOUS_CORROBORATION key — KeyError on every autonomous loop
  promote.
- **references_repo column drift** — was returning incomplete
  what_references results silently.
- **close_decision + archive_theory** take workspace_id for
  hub-mode defense.
- **promotion_bridge** None confidence coercion.

### Scope NOT touched (deferred to next session)

- Concurrency: shared sqlite3.Connection across HTTP+MCP+brain_pass
  is documented but not refactored.
- MCP-HTTP parity gaps (response shape, missing fields, SafeText
  bypass).
- record_with_evidence atomicity claim.
- Body size limit DoS guard.
- Performance N+1 batching in candidates_vector / recall /
  spreading_activation.

---

## 3.5.0 — 2026-05-20 (read-path hardening + ranker tuning + pipeline benchmark scaffold)

### Read-path enum-drift safety net

A live incident the morning of 2026-05-20 surfaced a class of bug
that had been latent for several minor releases: writers that
bypassed the repository helpers and INSERTed raw enum strings via
plain SQL would land values the row→model parser didn't recognise,
the parser raised ``ValueError`` deep inside ``build_context``, and
``/memory/get_context`` returned HTTP 500 with no actionable detail.
copyBot's memory brief was killed for ~2.5h before the cause was
isolated. Two writers were caught in the act: ``autonomous_loop``
emitting ``theory_evidence.kind='autonomous_corroboration'`` and
the code indexer emitting ``chunks.kind='block'`` / ``'symbol'``.

Two layered defenses now cover every read parser:

- ``models.enums.coerce_enum(EnumCls, raw, fallback)`` — universal
  helper that catches ``ValueError`` on unknown DB strings, handles
  non-string inputs (NULL, int, bytes), and returns the supplied
  fallback. Every ``row_to_*`` parser uses it.
- Seven previously-undocumented labels registered as first-class
  enum members so legitimate rows round-trip with full semantics:
  ``TheoryEvidenceKind.AUTONOMOUS_CORROBORATION``,
  ``ChunkKind.BLOCK``, ``ChunkKind.SYMBOL``,
  ``TrustLevel.USER_PROVIDED``, ``EpisodeSource.MANUAL``,
  ``EpisodeSource.USER_PROVIDED``, ``InsightType.CONSOLIDATION``,
  ``InsightStatus.CANDIDATE``.

Coverage by ``tests/unit/models/test_coerce_enum.py`` and
``tests/unit/repositories/test_theory_evidence_kind_tolerance.py``:
known values round-trip, unknown strings degrade to fallback,
NULL/int/list don't crash, every drift label parses correctly.

### Ranker tuning — MRR 0.3611 → 0.9889 on MemBench

The ``filter_rank_limit`` ranker used by ``/memory/list_decisions``
treated the pinned bit as an absolute sort key. With two pinned
decisions on the agent-memory-lite workspace they always sat at
ranks 1-2 regardless of the query, capping MRR at ~1/3 for any
non-pinned-target title-verbatim query. The v3.5 ranker:

- Browse mode (no query) keeps pinned-first ordering.
- Query mode demotes pinned to a content-score boost so a precise
  match still wins.
- Title-token weight is 3x body-token weight.
- All-tokens-in-title bonus (+5.0) gives exact matches the decisive
  lead over partial siblings.
- ``outcome_score`` contributes additively so low-outcome decisions
  sink relative to high-outcome ones at the same title score.

Locked by ``tests/unit/repositories/test_decisions_ranker.py``.
Baselines saved to ``scripts/calibration/baselines/membench_v3.4.0.json``
(0.3611) and ``membench_v3.5.0.json`` (0.9889) for regression checks.

### Pipeline-level BEIR benchmark — scaffolding

``scripts/membench_pipeline.py`` lands the third tier of MemBench:
the full retrieval stack (ingest → chunk → FTS5 → LanceDB → RRF →
reranker) measured against published BEIR qrels, complementing the
embedder-only ``membench_external.py`` (0.6694 on SciFact) and the
internal ``membench.py`` (MRR). The script ships with:

- Smart-slice mode: pick queries first, then ingest each query's
  relevant docs + 5x distractors so a 30-query run actually
  measures discriminative ranking instead of "is the doc in our DB".
- ``--db-path`` for throwaway DB routing via ``X-Memory-DB-Path``
  headers — keeps the host workspace clean. A v3.5 incident proved
  this matters: BEIR docs leaked into agent-memory-lite's DB before
  the isolation argument shipped.

Full-corpus runs take hours (HTTP ingestion is the long pole) and
are operator-initiated.

### Workspace pollution check — hub-mode false positive removed

``integrity_db_checks.workspace_pollution_check`` flagged ANY
foreign workspace_id as degraded, which produced a constant false
positive on hub-mode setups where one ``memory.db`` legitimately
serves multiple registered workspaces. The check now reads the
workspace registry, splits foreign rows into
``registered_foreign_rows`` (legitimate hub mates, status stays
``ok``) and ``unregistered_foreign_rows`` (real stray writes,
``degraded`` as before). Locked by two new tests in
``tests/unit/maintenance/test_integrity.py``.

---

## 3.4.0 — 2026-05-20 (multi-minor roll-up: brain loops + drift safety + observability)

### MemBench v2 — cross-project retrieval comparison

`scripts/membench_external.py` runs a BEIR task (default `SciFact`)
through our embedding model via `mteb` + `sentence-transformers` and
prints our number next to the published MTEB leaderboard. First live
run, 2026-05-20:

| System | NDCG@10 (BEIR SciFact) |
|---|---|
| **Our deployment (`intfloat/multilingual-e5-small`)** | **0.6694** |
| Published `e5-small` baseline | 0.68 (delta -0.011, within run variance) |
| BM25 lexical baseline | 0.67 |
| OpenAI `text-embedding-ada-002` | 0.72 |
| Cohere `embed-multilingual-v3.0` | 0.74 |
| `nomic-embed-text-v1` | 0.73 |

Our embedding is configured correctly (matches published number for
this model). The 5-7 NDCG@10 gap to ada/Cohere/nomic is the price of
the local-only constraint: e5-small is 118M params vs 350M-1B for the
commercial models. Trade-off is portable + zero-cost, accepted.

To run this benchmark: `pip install mteb` then
`python scripts/membench_external.py --task SciFact`.
Dataset (5MB) auto-downloads to `~/.cache/mteb/` on first run.
CPU runtime ~5-10 min. Result lands in `--save` JSON for tracking.



Catches the version string up to where the code has actually been
since 3.0.0. The intermediate v3.1 / v3.2 / v3.3 milestones (Vectors
1–6, UX hardening, adaptive-memory polish) all shipped on disk but
never bumped ``pyproject.toml`` / ``src/agent_memory_lite/version.py``
— ``/health`` was still reporting ``3.1.0`` while features past v3.4
were already running. This release closes that gap and rolls in the
v3.4 batch that landed in the 2026-05-20 session.

### v3.4 — what landed this session

**v3.4 #1 — autonomous_loop discipline.** ``_promote_to_theory`` in
``src/agent_memory_lite/cognition/autonomous_loop.py`` now synthesizes
two falsifiable predictions and two measurable validation_criteria
from the V1 proposal body before INSERT, and the SQL writes
``validation_criteria_json`` (previously missing from the column
list). Closes the audit gap that flagged every autonomously promoted
theory as ``undisciplined_active_theories``.

**v3.4 — drift_sentinel ``dangling_capability_links``.** Fourth
detector alongside fk_violations / fts_coverage / vector_coverage.
LEFT-JOINs ``capability_links`` against ``agent_roles`` /
``agent_skills`` / ``agent_playbooks``; emits
``memory_drift_capability_links`` the same brain_pass tick a
capability gets deleted, instead of waiting for the operator-run
audit window (which on 2026-05-20 missed 16 dangling rows for 10
days). Closes theory ``th_6bbb2cf024961a0f``.

**v3.4 #6 — hygiene action queue (full stack).** Operator-side
triage lifecycle on ``maintenance_events``: new ``action_status``
(open / claimed / dismissed / resolved) orthogonal to substrate
status, ``assigned_to`` / ``action_notes`` / ``claimed_at`` /
``dismissed_at`` columns (migration 0036 + 0037 backfill). Two new
HTTP routes (``/memory/claim_maintenance_event`` and
``/memory/dismiss_maintenance_event``), extended list filter
``action_statuses``. New ``/ui/queue`` page with claim / resolve /
dismiss buttons, filter selects (action_status / kind / severity),
nav link wired into all 8 sister UI pages. Opt-in Playwright
browser smoke (``MEMORY_QUEUE_E2E_URL``).

**v3.4 #7 — V4 method (b) Granger lead-lag causality.** Third
source for the causal_links table alongside DiD on supersedes
(``causal_did``, v3.3) and embedding similarity (``causal_embedding``,
v3.1). Builds per-day activity vectors from
``memory_usage_feedback`` over a 30-day window; for each ordered
pair (X, Y) with ≥3 non-zero buckets each, computes the lead-lag
gap ``|corr(X_{t-1}, Y_t)| - |corr(Y_{t-1}, Y_t)|`` and emits
``causal_link(relation='granger_caused', weight=|r_xy_lag|)`` when
the gap clears the threshold and the correlation is positive.
Pure-stdlib ``statistics.correlation`` — no statsmodels / numpy.

**v3.4 #8 — V5 LR sees audit_log churn.** Three new feature slots
appended to the V5 SGD-LR feature vector: workspace ``velocity``
(audit rows per day, normalized), ``edit_share`` (fraction of those
rows in the mutation set), ``agent_diversity`` (distinct agent_ids).
Computed AS OF each historical sample's ``created_at`` for
leakage-safe training. Model version bumped lr_v1 → lr_v2;
``audit_dim`` field on the saved model so predict can match. Live
training on agent-memory-lite produces non-zero audit-slot weights
(``-0.55 / -0.01 / -0.81``) — SGD finds real signal in churn.

**setup_agent.py --doctor.** Read-only scan of every workspace in
``~/.agent_memory/workspaces.json``: missing paths, deprecated
hook scripts in ``settings.local.json`` (the exact pattern that
silently killed copyBot's memory brief for ~2.5h on 2026-05-20 via
an ``inject_memory_context.py`` override), pending DB migrations,
MCP workspace_id mismatches, missing pretooluse enforcement rules.
Exit 0 healthy / 1 warnings / 2 critical; ``--json`` for CI piping.

**``db.migrations.apply_migrations`` bug fix.** The function
inserted rows into ``schema_migrations`` without ``conn.commit()``,
so migrations rolled back at the next connection close. Caught by
the new doctor — it flagged 0037 as still pending after two apply
runs. The fix adds an explicit commit; every workspace registered
in ``workspaces.json`` should now have its migrations stick.

### Earlier 3.x milestones (functionally present since 2026-05-19)

* **v3.1** — research vectors. Heuristic experiment proposal,
  adaptive retrieval, blindspot detection, predictive failure,
  embedding-based causality, inter-agent negotiation, cross-encoder
  reranker. Tasks #19–#46 in the project task tracker.
* **v3.2** — UX hardening from live audit. Tasks #51–#56.
* **v3.3** — adaptive memory push to 8.33/10 average. Tasks #57–#62
  (V1 min-evidence gate, V4 DiD multi-method, V5 LR with stdlib SGD,
  V6 disputes lifecycle, V2 transfer-learning bootstrap, V3 workspace
  stopword learning).

## 3.0.0 — 2026-05-19 (agent UX follow-ups)

Version-files alignment with the existing ``v3.0.0`` git tag
(``ad7f37d release(v3.0.0): memory as a brain — final``). The tag
landed without bumping ``pyproject.toml`` / ``src/agent_memory_lite/
version.py`` (both stuck at ``2.0.0``); this release closes the gap
and consolidates the post-tag agent-UX hardening into one shipped
3.0.0 baseline. Six new env-flags (``MEMORY_SUPPRESS_DEPRECATION_NOTICES``,
``MEMORY_STICKY_BRIEF_ENABLED``,
``MEMORY_STICKY_BRIEF_FOLLOWUP_TOKENS``,
``MEMORY_AGING_DECISIONS_ENABLED``,
``MEMORY_AGING_DECISIONS_DAYS``,
``MEMORY_AGING_DECISIONS_LIMIT``) documented in ``.env.example``.

### Agent UX follow-ups (5 fixes)

Five operator-friction items surfaced by a peer-agent audit on
2026-05-19; landed together because they share test infrastructure
and have no inter-feature coupling.

**1. Routing bug — MCP search returned empty for valid workspaces.**

All ten v3-strict handlers in
``src/agent_memory_lite/mcp/stdio_handlers_memory.py`` called
``_runtime.db()`` (anchor connection) instead of
``_runtime.db_for(workspace_id)`` (registry-routed). When the MCP
server's anchor was misconfigured (e.g. bound to ``copyBot`` while
the operator asked for ``agent-memory-lite``), every
``memory_search`` / ``memory_get`` / etc. silently routed to the
wrong DB and returned empty results. Replaced all ten sites with the
registry-routed accessor; regression tests
``test_search_routes_via_db_for_not_db`` +
``test_get_routes_via_db_for_not_db`` lock the contract.

**2. Deprecation-notice spam.**

The v2-to-v3 compat shim
(``src/agent_memory_lite/mcp/v2_compat.py``) attached a 70-token
``deprecation_notice`` to every legacy tool response. In long
sessions, hundreds of identical notices accumulated for no extra
information value. New behavior: emit the notice ONCE per legacy
tool name per process, plus a global suppress switch via
``MEMORY_SUPPRESS_DEPRECATION_NOTICES=true``. Module-level
``_seen_deprecations`` set tracks seen names;
``reset_seen_deprecations()`` is the test hook for clean slates.

**3. Move 5 — ``decision_neighbors`` in write_decision response.**

Mirrors the Move 3 ``capability_suggestions`` pattern. Every
``memory_write_decision`` and ``memory_record_with_evidence``
response now includes top-3 active decisions in the same workspace
whose tokens overlap the new write. Read-only hint — the agent can
choose to supersede an existing decision instead of fragmenting.
New module ``ingestion/decision_neighbor_suggester.py`` shares the
overlap-coefficient math with the capability suggester. Wired into
HTTP routes, MCP stdio handlers, in-process MCP tools, and the
local-fallback compound writer.

**4. Sticky-brief — adaptive ``max_tokens`` for long chats.**

``compose_brief`` now accepts ``session_id``. First call for
``(workspace_id, session_id)`` renders at the requested budget;
subsequent calls shrink to ``MEMORY_STICKY_BRIEF_FOLLOWUP_TOKENS``
(default 200). Cuts the per-prompt token tax in extended chat
sessions where the same brief auto-injects on every UserPromptSubmit.
Hook forwards ``session_id`` from the Claude Code event. Legacy
callers without ``session_id`` keep full-budget behavior.

**5. Aging-decisions sentinel — proactive "still active?" ping.**

New brief section ``## Aging decisions`` surfaces active, unpinned
decisions older than ``MEMORY_AGING_DECISIONS_DAYS`` (default 30)
with ``outcome_score == 0.0`` (no implicit/explicit feedback ever
landed). The agent sees them on session start and can revisit/
confirm/supersede rather than letting silent decisions accumulate.
Read-only — never degrades outcome or archives. New module
``maintenance/aging_decisions.py``; brief composer adds a 2% budget
slot for the section.

**Env-flag additions:** ``MEMORY_SUPPRESS_DEPRECATION_NOTICES``,
``MEMORY_STICKY_BRIEF_ENABLED``,
``MEMORY_STICKY_BRIEF_FOLLOWUP_TOKENS``,
``MEMORY_AGING_DECISIONS_ENABLED``,
``MEMORY_AGING_DECISIONS_DAYS``,
``MEMORY_AGING_DECISIONS_LIMIT``. All documented in ``.env.example``.

**Quality gates green:** ruff check + format clean; mypy strict
clean across 602 source files; full pytest (excluding ollama-
dependent + crash tests) — 1900+ passing. SLOC check: new files all
under the 150-line cap.

**Operator note.** After ``git pull``, restart Claude Desktop /
Cursor / VS Code (MCP stdio servers don't auto-reload) AND restart
the HTTP service so all five fixes take effect.

**Audit-driven hardening (peer review by separate AI agents).**

After the five fixes above landed, two parallel audit agents reviewed
the diff and surfaced critical findings I missed:

* **Legacy MCP handler routing.** The v3-strict routing fix only
  covered 10/40 handler sites. Every legacy v2 MCP handler
  (``stdio_handlers_episodes.py``, ``_decisions.py``, ``_theories.py``,
  ``_capabilities.py``, ``_capability.py``, ``_research.py``,
  ``_review.py``, ``_archive.py``, ``_p1.py``, ``_state_snapshots.py``,
  plus the ``compat_dispatch`` entry in ``stdio_server.py``) was still
  calling ``_runtime.db()`` directly — same silent-wrong-DB bug class.
  Applied the same ``db_for(workspace_id)`` pattern to all 30+
  remaining sites. Handlers that operate on globally-unique ids
  (promote_candidate, reject_candidate, resolve_maintenance_event)
  accept an OPTIONAL ``workspace_id`` and route via registry when
  provided; fall back to anchor when not.

* **Silent fallback warning.** ``_runtime.db_for`` previously fell
  back to the anchor connection without any signal when the requested
  workspace_id was unknown to the registry. That hid exactly the
  misconfiguration the routing fix addresses. Added warn-once
  logging keyed on the unresolved id; tests
  ``test_stdio_runtime_db_for_warn.py`` lock the once-per-id +
  per-process semantics + the anchor-self short-circuit.

* **Aging-decisions section budget bumped.** Audit caught that the
  initial weight (0.02 = 10 tokens at the default 500 budget)
  couldn't fit even the section header plus one row. Rebalanced to
  0.04 (~20 tokens), trimming ``associates`` and ``recent_insights``
  by 0.01 each to preserve the 1.00 sum invariant.

* **``age_days`` cosmetics.** Switched from
  ``int(timedelta.total_seconds() / 86400.0)`` to ``timedelta.days``
  for clearer intent; unparseable ``valid_from`` now returns the
  ``-1`` sentinel instead of falsely reporting ``age_days==threshold``.

* **Test coverage expanded.** Added
  ``test_decision_local_fallback_surfaces_decision_neighbors``
  (Move 5 contract on the in-process MCP path),
  ``test_limit_env_override`` /
  ``test_age_days_uses_native_timedelta_days`` /
  ``test_age_days_sentinel_on_unparseable_timestamp`` for the aging
  module, and the entire ``test_stdio_runtime_db_for_warn.py`` file.

After hardening: full pytest still passes (1900+ tests), ruff +
mypy strict still clean across 602 source files.

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
ship from the same checkout. v3 endpoints mount at `/memory/*`,
v3 MCP tools are prefixed `memory_*`, v2 surface keeps working
unchanged until the week-8 canary flip.

### Foundation (Phase 1)

- **`migrations/canonical/0001_init.sql`** — consolidated DDL replacing
  v2 migrations 0001 + 0020-0032. 35 tables: 12 core kinds
  (decisions, theories, behaviors, skills, concepts, tasks,
  episodes, insights, code_digests, chunks, theory_evidence,
  versions) + audit_log + FTS5 virtual tables + indexes.
  `gist` / `*_one_line` / `*_short` columns persisted on write
  so projections are pre-computed.
- **`scripts/migrate_to_canonical.py`** — idempotent SQLite-to-SQLite
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

- **HTTP `/memory/*`** — 13 endpoints returning uniform
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
