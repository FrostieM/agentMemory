# Changelog

All notable active-line changes to **agent-memory-lite**.

This file intentionally tracks only the current v3 line. Older pre-v3
development history was removed from the active tree; use git history when
archaeology is required.

Versioning follows semver. Minor bumps add functionality; patch bumps fix
bugs without behavioral expansion.

## 3.16.0 - 2026-05-29

### Changed

- `storage.reader.search` now gives a focused, explicit `kinds=[...]` query the
  full `limit` budget *per requested kind* instead of `limit // len(kinds)`
  (release task #120). A two-kind search (`kinds=['decision','behavior']`,
  `limit=10`) previously capped each kind at 5, so a query whose strongest
  matches were all decisions could return at most 5 of them and silently drop
  the rest below the fold. A default wide search (`kinds=None`) keeps the
  divided budget so no single kind crowds out the others; the overall result is
  still bounded by `limit`. Pinned by
  `tests/unit/storage/test_search_per_kind_budget.py`.

## 3.15.0 - 2026-05-29

### Added

- `memory_status` environment block now reports HF offline posture:
  `hf_auto_offline` (the setting) and `hf_offline_active` (computed live from
  `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE`), via the single-source
  `offline_bootstrap.hf_offline_active()` helper — so an operator can confirm
  the model hub is actually pinned offline without reading the process env.

## 3.14.0 - 2026-05-29

### Added

- Durable code-digest refresh in the brain pass
  (`maintenance/digest_refresh.refresh_stale_digests`): each tick re-verifies a
  bounded, rotating batch (`MEMORY_DIGEST_REFRESH_MAX_PER_PASS`, default 50) of
  the least-recently-checked `code_digests` and recomputes the ones whose
  `file_sha1` drifted from the file on disk, so an `impact_check` stale verdict
  self-heals without waiting for a full audit or relying on the 120s pre-commit
  ingest ceiling. Bounded (O(limit)/pass, not an O(repo) tree walk), rotating
  (bumps `last_indexed_at` on every digest checked), failure-soft, with bounded
  retries on transient read / DB-lock errors. Gated by
  `MEMORY_DIGEST_REFRESH_ENABLED` (default on); a workspace with no registered
  project root is a no-op. Missing/orphan-digest detection stays with the
  heavier audit scan.

## 3.13.0 - 2026-05-29

### Added

- The MCP stdio server warms the embedding model in a background daemon thread
  at startup (`MEMORY_MCP_WARM_EMBED`, default on), so the first tool call that
  needs an embedding no longer pays the ~1.5s cold model-load. Failure-soft,
  and runs after the offline guard so the warm load stays cache-only.

### Changed

- `SentenceTransformersProvider._load` is now thread-safe (double-checked
  locking with safe `_dim`-before-`_model` publication ordering): the provider
  is a per-process singleton shared across the MCP handler threads
  (`asyncio.to_thread`) and the new warm-up thread, and the prior
  check-then-set could let two threads both load the model.

## 3.12.0 - 2026-05-29

### Changed

- Backup retention for `.agent_memory/backups`: the operator scripts that
  snapshot the DB (and, for `memory_audit` / `bulk_index_codebase`, the whole
  `vectors.lance` store) before a risky operation now prune their own backup
  family to the newest `DEFAULT_KEEP` (5) snapshots via the shared
  `maintenance/backup_retention.prune_backups`, capping the previously
  unbounded growth (observed ~20GB; issue `issue_201c0b47be474319`). Pruning is
  prefix-scoped (only a script's own backup family is touched), **protects the
  just-written snapshot** (a pre-repair backup is never deleted in the run that
  created it), and is failure-soft. Going-forward only — existing backups are
  not deleted (that needs explicit operator action). Sidecar `.bak-*` files
  written beside the DB by `repair_dangling_source_refs` /
  `repair_corrupted_theory_fields` are a separate, smaller vector not yet
  capped.

## 3.11.0 - 2026-05-29

### Added

- HuggingFace offline-by-default at startup (`config/offline_bootstrap.py`):
  once the embedding model is confirmed in the local HF cache, the HTTP and MCP
  entrypoints set `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE` to `1` by default —
  defense-in-depth over the per-load `local_files_only=True` (the primary
  local-only control), covering transitive `huggingface_hub` / `transformers`
  paths. The cache probe is an import-free filesystem check (so it cannot
  freeze HF's offline constant before the env var is set) and requires a real
  weight file, so a partial download is treated as "not cached" and the
  one-time bootstrap window stays open. Gated by `MEMORY_HF_AUTO_OFFLINE`
  (default on); an explicit operator-set `HF_HUB_OFFLINE` always wins; scoped
  to the embedding model (the opt-in reranker is failure-soft).
- Kind-registry drift-guard test (`tests/unit/storage/test_kind_registry_consistency.py`):
  asserts every agent-facing memory kind is wired across the MCP tool enum,
  writer `_KIND_META`, reader `_KIND_TABLES`, and projection `_PROJECTORS`, and
  that writer/reader tables agree — failing fast on the "added to one map but
  not another" class instead of shipping a half-wired kind.

## 3.10.0 - 2026-05-28

### Added

- First-class `issue` memory kind — a durable bug / tech-debt / risk / error
  registry (migration `0002_issues.sql`) wired through the storage write,
  projection, reader, and MCP-tool surfaces. Searchable via `memory_search`
  and surfaced as an "Open issues (debt/risk)" section in `memory_brief`,
  ordered by severity.
- Signature-based issue dedup (`ingestion/issue_writer.py`): re-observing an
  open defect returns the existing row; a recurrence after close opens a new
  one. Lifecycle transitions (open → in_progress → fixed / wontfix / accepted)
  ride the versioned + audited generic edit path.
- Auto-capture of hygiene findings (>= major) into the issue store
  (`maintenance/issue_capture.py`), exposed as the opt-in
  `memory_audit.py --capture-issues` flag (no-ops gracefully on a
  pre-migration database).

## 3.9.0 - 2026-05-28

### Added

- Code-memory freshness checks (`maintenance/code_memory_freshness.py`) and
  shared source-scan helpers (`cognition/codebase_scan.py`) so the audit
  surfaces stale or missing code digests.
- Memory-hygiene checks for duplicate decisions, low-signal insights, windowed
  duplicate episodes, and orphaned or closed-task plan steps
  (`maintenance/hygiene_duplicate_checks.py`).
- Low-signal insight filters (`utils/insight_filters.py`): regex/length
  heuristics that suppress auto-consolidation noise (file-indexed placeholders,
  tiny recurring-theme clusters), used by both the brief and hygiene.
- `MEMORY_LLM_EXTRACT_TIMEOUT_SEC` (default 10s) bounds the inline Ollama
  extractor on the synchronous episode-write path ("abort after T").
- `Glob` and `NotebookRead` added to the PreToolUse hook matcher so the
  impact-check-before-read discipline covers every read-side tool.

### Changed

- The embedding provider and the cross-encoder reranker now load models
  cache-only (`local_files_only=True`) with a one-time networked bootstrap
  fallback. A cached model loads with zero network traffic, honoring the
  local-only ("no cloud calls") contract.
- The PreToolUse hook matcher is sourced from a single canonical
  `HOOK_MATCHERS` tuple shared by `setup_agent.py`, `install_memory_hooks.py`,
  and the setup doctor, so the install and drift-detection paths cannot
  diverge.

### Fixed

- A hung `memory_write`: the embedding model load made an unbounded
  huggingface.co network call (no app-level timeout) on the synchronous write
  path; cache-only loading removes it, and the inline LLM extractor is now
  capped (see `MEMORY_LLM_EXTRACT_TIMEOUT_SEC`).
- Stripped a UTF-8 BOM from `migrations/0001_init.sql`.
- Corrected a stale matcher-token order in the `pre_tool_use_check.py`
  docstring.

## 3.8.0 - 2026-05-27

### Changed

- The repository now has a v3-only active surface: compact MCP tools,
  canonical v3 tables, root migrations, and one migration runner.
- Agent-facing setup docs now describe `memory_write(kind=...)`, compact
  projections, `memory_impact_check`, and plan-step discipline.
- `setup_agent.py` installs the registry-routed v3 hook stack for both Claude
  and Codex project configs.
- Startup, setup, bootstrap, and HTTP paths use the root migration runner and
  the consolidated v3 init schema.

### Fixed

- Project cwd/subdirectory resolution now prefers registered project anchors
  before falling back to hub mode, preserving strict workspace isolation.
- The setup doctor now checks active project `CLAUDE.md` / `AGENTS.md` files
  and Codex hooks for stale legacy memory contracts.
- `copyBot` project contracts and hooks were refreshed to the v3-only contract
  so agents receive memory automatically from the project files.

### Removed

- Legacy v2 storage tables, route modules, compatibility docs, and setup
  wording from the active project surface.
- `migrations/canonical/`; the root migration chain is the single source of
  truth.
- The legacy changelog document; historical details remain available through
  git history.

### Verification

- The release package is gated by ruff format/check, mypy, setup doctor,
  `scripts/v3_surface_check.py`, `scripts/memory_contract_check.py --strict`,
  MCP smoke checks for `agent-memory-lite` and `copyBot`, the eval suite, SLOC
  enforcement, `git diff --check`, and full pytest.

## 3.7.1 - 2026-05-22

### Fixed

- Cross-workspace ingest-leak guard. Write routes now verify that the physical
  SQLite connection matches the target registered workspace before rows are
  written.

## 3.7.0 - 2026-05-21

### Added

- Orphan-vector pruning in the brain pass.

### Fixed

- MCP local-only guard parity with the HTTP service.
- Several cross-workspace write holes.
- Origin and host checks for local browser safety.
- Consolidation noise filtering.
- CI and setup drift around the workspace manifest and `.env`.

## 3.6.0 - 2026-05-20

### Fixed

- Full-project adversarial audit across storage, retrieval, ingestion,
  API/MCP, cognition, UI, scripts, and tests.
- Local-only security hardening, redaction gaps, enum coercion failures,
  workspace routing mistakes, UI XSS risks, and maintenance/test reliability
  issues found by the audit loop.

## 3.0.0 - 2026-05-19

### Added

- v3 compact-projection memory surface.
- Outcome scoring, Hebbian co-retrieval edges, consolidation, reflex rules,
  self-model briefs, bi-temporal filtering, and spreading-activation recall.
- Observatory, recall, reflexes, metrics, review, browse, code, and graph UI
  pages.

### Changed

- Project setup defaults to v3 memory, root migrations, workspace registry
  routing, and strict project isolation.
