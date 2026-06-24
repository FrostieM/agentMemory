# agent-memory-lite — Project & Architecture Scorecard

Honest self-assessment with explicit **gap-to-10/10 tracking**. This is *not* a
claim of "10/10" or "production-ready" — by the project's own discipline a
production claim requires ≥3 adversarial audit rounds returning **zero new
findings** *and* a green pipeline. Adversarial rounds were run per feature this
cycle and their findings were fixed, but a 3-consecutive-zero-new-findings
streak across the whole system has **not** been certified. This document tracks
where we stand and the remaining gaps.

- **As of:** v3.22.0 — released and **pushed to origin/main with green CI** (the
  full pipeline: install, ruff, mypy, check_sloc, v3-surface, tests, retrieval
  evals, agent-contract checks). Every score below is for current `main`.
- **Grounding (verified):** `pytest` collects 2812 tests (green this cycle);
  `ruff`/`ruff format` clean; `mypy --strict` clean on 538 source files;
  `scripts/crash_test` has 27 phases; the 150-SLOC ceiling
  (`check_sloc.py --enforce`) is green; migrations `0001`–`0007`; v3 surface/
  contract checks pass.

## What the project is

A local-first memory subsystem for AI coding agents. SQLite (WAL + FTS5) is the
source of record, LanceDB stores vectors, sentence-transformers does CPU
embeddings, Ollama is the optional local LLM extractor. It exposes a FastAPI
HTTP surface and an MCP stdio surface (12 compact v3 tools), binds to
`127.0.0.1` only, and runs a background "brain pass" of maintenance loops.

## Scorecard (per dimension)

Scores are an internal read of the local line. A harsh external reviewer would
likely land **~7.5** overall (see "Overall" below) — chiefly because production
DBs are mid-migration (#122).

| Dimension | Score | Gap to 10/10 |
|---|---|---|
| Architecture & code structure | 9.0 | LLM extraction + embedding still on the synchronous write path (10.1). |
| Reliability & robustness | 8.0 | Sync-write bounded but slow; a vector upsert can fail post-commit (row lands, vector absent from the embedding store until reindex); prod DBs half-migrated (#122). |
| Local-only / privacy / security | 9.0 | `ensure_workspace_matches_db` not yet on 100% of write routes (#115); loopback relax is intentional. |
| Retrieval quality | 7.5 | No automated MRR-regression gate (10.6); FTS5/LIKE-only read path (no vector arm in search); committed-row-without-vector window touches only vector-consuming paths (write-time dedup + brain-loop causal/Hebbian), not the FTS read path. |
| Cognition (brain loops + discipline) | 9.0 | Some loops heuristic; LLM-consolidation opt-in; discipline read-check is once-per-session + fails open. |
| Testing & quality gates | 8.5 | UI not live-verified (10.8); plan UI unbuilt (#110); no retrieval-regression gate. |
| Operability & observability | 7.5 | Offline posture absent from `memory_status`; UI refresh pending (10.7); backup retention is manual-trigger, not a sweep. |
| Performance & scale | 7.0 | Single-machine; sync-write latency; `vectors.lance` still copied into backups; LanceDB compaction TODO. |
| Docs & developer experience | 8.0 | A few env vars still under-documented. |
| **Overall (internal)** | **~8.0** | Harsh-external floor ~7.5. See "Roadmap to 10/10". |

### Notes per dimension

**Architecture (9.0).** Clean layered design (`api / ingestion / retrieval /
cognition / maintenance / repositories / storage / db / mcp`), small focused
modules under a SLOC ceiling, a single v3-only surface, forward-only SQL
migrations with a baseline-validation guard, and a generic kind-write path now
protected by a registry-drift guard test. The real architectural debt is the
synchronous write doing inline LLM extraction + embedding (10.1).

**Reliability (8.0).** This cycle removed the dominant failure: a hung
`memory_write` from an unbounded huggingface.co load — fixed via cache-only
model loads, HF-offline-by-default once cached, an app-level Ollama timeout, and
a thread-safe + warmed MCP provider. Every brain-pass step and background worker
is failure-soft. Honest residuals: the write path is bounded but still
synchronous (10.1); the vector upsert runs *after* the SQLite commit and is
failure-soft, so a write can return success with the row durable but **no vector
written** (surfaced only as a `vector_upsert_failed` maintenance event) until
`reindex_vectors.py` runs; and production DBs are reportedly half-migrated
v2→v3 (#122) — a real drag.

**Local-only / security (9.0).** `assert_local_only` runs at startup (HTTP +
MCP): an unconditional cloud denylist + telemetry kill-list, loopback
enforcement, proxy/provider-host env-var auditing, and HF offline-by-default.
SQLite source of record; server-side redaction; strict workspace isolation
(foreign-workspace writes blocked). Caveats: the loopback requirement is
intentionally relaxable for on-prem fronting, the one-time model bootstrap from
huggingface.co is a documented exception, and the `ensure_workspace_matches_db`
physical-DB guard is **not yet applied uniformly to every write route** (#115) —
so isolation is strong but not yet fully defense-in-depth.

**Retrieval (7.5).** BM25/FTS5 over chunks + durable kinds with a LIKE
token-overlap fallback, score-band sorted, plus an opt-in cross-encoder reranker
(no live RRF fusion or vector arm in the v3 read path); MRR was tuned and a pipeline-level BEIR benchmark exists
(well-hedged: not yet a gating, continuously-tracked signal). Gaps: no scheduled
MRR-regression gate (10.6); and because vectors are written post-commit and
failure-soft, a freshly-written row can be absent from the embedding store —
which feeds write-time episode dedup and the brain-loop causal / Hebbian passes,
not the FTS-only search read path — until a manual reindex.

**Cognition (9.0).** The background brain pass runs ~19 independent,
failure-soft maintenance loops (outcome scoring, Hebbian co-retrieval
distillation, insight→behavior promotion, reflex distillation, self-model
refresh, causal extraction, DiD + Granger causality, predictive-failure scan,
predictive-LR training, drift sentinel, dead-behavior auto-archive, plan
playbook/outcome distillation, vector-prune, and the new digest-refresh). Recall
adds bi-temporal filtering and spreading-activation (retrieval-time features,
not brain-pass loops). Distinctively, the project couples this with
*discipline-enforcing* PreToolUse hooks: mechanical rules (impact-check-before-
read, read-before-edit, search-before-architectural-write) **block** the tool
call deterministically (hook exits non-zero). Caveats: the read-side check is
once-per-session (any prior impact-check authorises subsequent reads), and the
hook **fails open** if memory is unreachable (so it never bricks the agent).

**Testing (8.5).** 2812 collected tests, paired tests for non-trivial behavior,
a 27-phase crash test, an eval suite, SLOC enforcement, and the multi-gate
release check. This cycle's features were each mutation-tested by adversarial
agents. Gaps: UI pages are not verified to render live (10.8), the plan UI is
unbuilt (#110), and there is no retrieval-regression gate.

**Operability (7.5).** `memory_status`, `/health`, audit/doctor/trust-dashboard
scripts, and per-pass brain telemetry. Gaps: the HF offline posture isn't in
`memory_status`; backup retention is **creator-triggered** (it prunes only when
an operator script runs, not as a continuous background sweep); the UI refresh
(10.7) is pending.

**Performance/scale (7.0) — clearly the weakest.** Bounded background loops, DB
indexes, a warmed embedding model, and a bounded/rotating digest-refresh keep
per-tick cost flat — though that rotation assumes brain passes land in distinct
second-resolution timestamps (true at the multi-hour cadence). But it is
single-machine, the sync write pays embedding/LLM cost inline, the rebuildable
`vectors.lance` is **still copied** into backups (retention caps it, doesn't
stop it), and LanceDB compaction is a TODO.

## Uniqueness vs. alternatives (honest)

Compared to mem0, Letta/MemGPT, Zep, LangMem, and plain vector-RAG:

**Differentiators / stronger**
- **Hard local-only, no-cloud guarantee** — enforced in code (denylist +
  offline-by-default), not just a config toggle. Uncommon among memory layers.
- **Memory coupled with agent-discipline enforcement** — the distinctive trait
  is the *coupling*: the same system that stores memories also runs PreToolUse
  hooks that block undisciplined tool calls. Tool-gating exists elsewhere
  (editor/agent-framework rules and guardrails), but pairing it with the memory
  store is uncommon.
- **Cognitive loops run locally** — outcome/Hebbian/consolidation/causal
  learning as a background pass, no external service.
- **Token-frugal agent surface** — compact ~30-token projections + a 12-tool
  MCP surface designed for an agent's context budget.
- **Framework-agnostic** — plain HTTP + MCP.

**Weaker / less mature**
- **No managed cloud / horizontal scale** — single-machine by design; not a
  hosted multi-tenant service like Zep or mem0 cloud.
- **Smaller ecosystem & fewer integrations** than the larger projects.
- **Benchmarks not gating** — MemBench/BEIR exist but aren't a continuous signal.
- **No full entity knowledge graph** (has bi-temporal + causal links, not Zep's KG).
- **Windows-centric dev surface.**

## Roadmap to 10/10 (prioritized)

1. **10.1 — async write path** (biggest lever): persist synchronously, move LLM
   extraction + embedding to a background worker. Deferred to a focused session.
2. **#122 — finish the v2→v3 production-DB migration** (half-migrated prod is a
   real risk and the biggest drag an external reviewer would apply).
3. **#115 — apply `ensure_workspace_matches_db` to every write route** (closes
   the partial-isolation gap behind the security score).
4. **10.6 — MRR-regression gate** so retrieval quality can't silently regress.
5. **10.7 / 10.8 / #110 — build the plan UI, refresh the observatory, and
   live-verify every page.**
6. **6 — fresh-agent acceptance test;  7 — incremental vector repair +
   trust-dashboard performance.**
7. Smaller: HF offline posture in `memory_status`; stop backing up the
   rebuildable `vectors.lance` + add LanceDB compaction; make retention a
   continuous sweep.

## Bottom line

A genuinely strong **local-first, discipline-enforcing, self-maintaining**
agent-memory system whose standout traits are hard no-cloud enforcement, the
local cognitive loops, and memory coupled with behavioral discipline hooks.
Reliability took a large step up this cycle — the **dominant hang class is
closed**. It is **not** at 10/10: the headline gaps are the synchronous write
path, the unfinished production migration (#122), partial write-route isolation
(#115), no retrieval-regression gate, and unbuilt/unverified UI. Internal read
~8.0; a harsh external reviewer scoring it against a mid-migration prod would
land ~7.5.
