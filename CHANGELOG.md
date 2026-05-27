# Changelog

All notable active-line changes to **agent-memory-lite**.

This file intentionally tracks only the current v3 line. Older pre-v3
development history was removed from the active tree; use git history when
archaeology is required.

Versioning follows semver. Minor bumps add functionality; patch bumps fix
bugs without behavioral expansion.

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
