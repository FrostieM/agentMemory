# SESSION_STATE.md - agent-memory-lite

Rolling state for cross-session work. Pair-read with `AGENTS.md` and
`CHANGELOG.md`.

## Current State - v3-Only Refactor Release Package

The active goal is a 10/10 v3 project state. The implementation is now in
release-readiness review for version `3.8.0`:

- v3 compact tools are the only active agent surface.
- Root migrations are the only active migration chain.
- v3 canonical tables are the default storage target.
- Legacy routes, docs, compatibility names, and duplicate runners are removed
  from active project space instead of kept as working context.
- Claude and Codex project hooks use the registry-routed v3 brief, pre-tool,
  and post-edit stack.
- `copyBot` has been refreshed to the same v3-only project contract so agents
  should auto-load memory from project files after a new agent session starts.

## Active Plan

1. Keep final release checks green for the `3.8.0` package.
2. Do not commit, tag, push, or deploy until the operator explicitly asks.
3. After source edits, restart already-open MCP stdio clients before trusting
   that they are running the new code.
4. After API or routing changes, restart the HTTP service before runtime
   validation.

## Verification Bar

Before commit or release, keep all of these green:

- `ruff format --check .`
- `ruff check .`
- `mypy src`
- `python scripts/check_sloc.py --enforce`
- `python scripts/run_evals.py --workspace default --no-vector`
- `python scripts/v3_surface_check.py --json`
- `python scripts/memory_contract_check.py --strict --json`
- `python scripts/setup_agent.py --doctor --json`
- `python scripts/memory_mcp_smoke.py --workspace agent-memory-lite --require-behavior --require-capabilities --json`
- `python scripts/memory_mcp_smoke.py --workspace copyBot --require-behavior --require-capabilities --json`
- `pytest -q`
- `git diff --check`
- At least three clean adversarial audit rounds by separate agents for the
  touched section set.

## Operator Notes

The working tree is intentionally large because this is a full v3-only
refactor. Treat unrelated dirty files as part of the shared project state; do
not revert them automatically.
