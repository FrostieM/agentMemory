# V3 Removed — what gets dropped at the week-8 cutover

This is the kill-list for the v3.0.0 final cutover. Total: ~9,500 SLOC removed
from the v2 codebase, ~22% of the source surface.

> Status: deletions happen during week-8 of the v3 plan. The list below is the
> commitment, not a description of work already done. Track progress against
> this doc in the v3 cutover commit.

## Why these are removed

The v2 codebase grew brainically over a year. The audit findings (see
`POST_V2_ROADMAP.md`) identified:

* **150-SLOC/file cap forced over-fragmentation.** v3 replaces it with
  ≤80 LOC/function + one concern/file. Many forced splits collapse back.
* **30+ MCP tools were aspirational; only 6 are needed.** The other 24 became
  shimmed v2 names that route to v3 backends, or are dropped entirely.
* **70 MCP files duplicated handler + schema per tool.** v3 collapses to one
  file per domain (`stdio_tools_memory.py` + `stdio_handlers_memory.py`).
* **17 always-on env flags are now defaults.** Inline'd, env vars dropped.

## Categories

### 1. One-off scripts (~3,200 SLOC)

| Script | Reason | Replacement |
|---|---|---|
| `scripts/tier0_*.py` (8 files) | Tier-0 trial scripts from v1.0 | Folded into v3 acceptance gate |
| `scripts/v2_1_followup.py` | One-off v2.1 cleanup | Done |
| `scripts/verify_v215.py` | One-off v2.1.5 verification | Done |
| `scripts/post_bug_fix_reset.py` | One-off post-bugfix reset | Done |
| `scripts/repair_*.py` (4 files) | Ad-hoc repair scripts | Folded into `memory_audit.py --repair` |
| `scripts/crash_test_v3.py` (monolith) | Single-file crash test | Replaced by `scripts/crash_test/` package |

### 2. MCP handler / schema duplication (~1,800 SLOC)

* 70 MCP files → ~20: each domain currently has separate `stdio_tools_*.py` +
  `stdio_handlers_*.py` + `tools_*.py` + `tools_registry_*.py` modules.
* v3 collapses to one tools file + one handlers file per domain.
* v2 tool names that have v3 backends stay accessible via `mcp/v2_compat.py`.

### 3. Retrieval context builder fragmentation (~700 SLOC)

* `retrieval/context_builder_*.py` (24 files) → 6 files.
* The "envelope" concept is replaced by the v3 brief composer, which is
  single-file (`v3/cognition/brief.py`).

### 4. Maintenance modules (~700 SLOC)

* `maintenance/integrity_*.py`, `hygiene_*.py`, `quality_gate_*.py`,
  `sentinel_*.py` (27 files) → 8 files.
* v3 consolidates the recurring checks into:
  * `scripts/memory_audit.py` (one entrypoint for all integrity probes)
  * `scripts/memory_hygiene.py` (one entrypoint for all content-quality
    findings)
  * `v3/cognition/consolidation.py` (sleep-time clustering, replaces the
    daily distillation cron)

### 5. HTTP routes (~800 SLOC)

* `api/routes/` (83 files) → 8 files (just `v3/api/routes.py` + a handful
  of preserved v1 routes for the legacy admin UI).

### 6. Repositories / bootstrap / research_writer consolidations (~700 SLOC)

* `repositories/` (26 files) → 12 files matching the 12 core v3 tables.
* Bootstrap scripts merged into a single `scripts/bootstrap_workspace.py`.

### 7. Always-on env flags (~100 SLOC)

The following env vars become defaults in v3 and the corresponding guard code
is inlined:

* `MEMORY_EPISODE_DEDUP_ENABLED` (default on since 1.1.0)
* `MEMORY_CONFIDENCE_DECAY_ENABLED` (1.1.0)
* `MEMORY_CONFLICT_DETECT_ENABLED` (1.1.0)
* `MEMORY_FEEDBACK_EWMA_ENABLED` (1.4)
* `MEMORY_IMPLICIT_FEEDBACK_ENABLED` (2.1)
* `MEMORY_CAPABILITY_MATURITY_ENABLED` (1.5)
* `MEMORY_BEHAVIOR_APPLY_TRACKING_ENABLED` (1.5)
* `MEMORY_COLD_TRACKING_ENABLED` (1.6)
* `MEMORY_COLD_AUTO_QUEUE_ENABLED` (1.6)
* `MEMORY_THEORY_BRIDGE_ENABLED` (1.7)
* `MEMORY_REFLECTIVE_COMPACT_ENABLED` (1.8)
* `MEMORY_HYGIENE_PERSIST_ENABLED` (1.9)
* `MEMORY_SENTINEL_PERSIST_ENABLED` (1.9)
* `MEMORY_CORRECTION_DETECT_ENABLED` (1.10)
* `MEMORY_CORRECTION_TRANSCRIPT_READ_ENABLED` (1.10)
* `MEMORY_HUB_MODE` (default-on when registry has multiple entries)

Total env var count: 71 → ≤30.

### 8. SQL migrations consolidated (~750 SLOC)

* `migrations/0001_init.sql` + `0020_*.sql` … `0032_*.sql` (14 files,
  one starting point + 13 forward-only migrations) → `migrations/canonical/0001_init.sql`
  (single consolidated DDL).
* Migrations 0002-0019 were lost in v2 forward-only flow; v3 ignores them
  entirely.

### 9. Legacy docs (~120 KB)

| Doc | Status |
|---|---|
| `V1_1_0.md` | Replaced by `V3_AGENT_RUNTIMES.md` |
| `V1_1_0_CALIBRATION.md` | Archived to git history |
| `V1_2_0.md` | Replaced by `V3_AGENT_RUNTIMES.md` |
| `V1_4_TO_V2_ROADMAP.md` | Archived |
| `V2_CALIBRATION.md` | Replaced by v3 acceptance-gate report |
| `POST_V2_ROADMAP.md` | Source for the v3 plan; archived |
| `CHANGELOG_LEGACY.md` | Archived |
| `docs/demo.gif` (~80 KB) | Replaced by `docs/screenshots/v3_*.png` |

## What's preserved (the v2 reuse list)

See [v3 plan §Reuse List](../README.md#) — ~2,800 SLOC unchanged + ~5,000 SLOC
refactored. Key survivors:

* `db/migrations.py` runner (forward-only, reused verbatim)
* `redaction/*` (5 files, 110 SLOC, runs before every v3 write)
* `config/local_only_guard.py` (cloud denylist)
* `config/workspace_registry.py` (extended with `mode` field)
* `extraction/symbol_edges_*` (tree-sitter, feeds the digest worker)
* `enforcement/dispatch.py` (memory_lint is wired through it)
* `embeddings/*` + `vector_store/lancedb_store.py` (unchanged)
* `utils/*` (ids, time, hashing, pathing, tokens — 163 SLOC verbatim)
* `compaction/*` (base for sleep-time cron; SQL sink unchanged)
* Tests: `tests/invariants/test_v2_parity.py`, `tests/property/test_*_invariants.py`
  ported as parity gates; v3 must pass them.

## See also

* [`V3_SCHEMA.md`](V3_SCHEMA.md) — what stays in
* [`V3_MIGRATION.md`](V3_MIGRATION.md) — how to cutover
* [`V3_AGENT_RUNTIMES.md`](V3_AGENT_RUNTIMES.md) — what your agent sees on v3
