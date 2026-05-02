# ADR 0004: Hub mode + workspace registry

Status: accepted (2026-05-01)

## Context

Each project on a developer machine has its own per-project memory at
`<project>/.agent_memory/{memory.db, vectors.lance}`. With one project on
the machine the original single-anchor model worked: `setup_agent.py
--project` baked `MEMORY_DB_PATH`, `MEMORY_WORKSPACE_ID`, and
`MEMORY_FORBID_DEFAULT_WORKSPACE=true` into `<project>/.claude/settings.json`,
the spawned MCP server talked to that one DB, and a single launcher
script (`start-copybot-memory.ps1`, `serve.py`) hosted the HTTP service
for that one workspace.

Once a second project (`agent-memory-lite` itself) needed memory, the
single-anchor model started failing in two ways:

1. The HTTP service on `127.0.0.1:8765` could only ever serve one
   workspace at a time. A `UserPromptSubmit` hook fired from the other
   project sent `workspace_id="agentLight"` at a service anchored to
   `copyBot` and got `400 Bad Request: workspace_id='default' is
   disabled by MEMORY_FORBID_DEFAULT_WORKSPACE`.

2. The local UI at `/ui` had no way to switch between project memories
   without restarting the service with different env vars.

Three options were considered:

- **A. One service per project, on different ports.** Simple isolation
  but every project needs a port allocation, the user has to remember
  which port goes with which project, and `inject_memory_context.py`
  has to learn how to discover the right port. Bookkeeping doesn't
  scale.

- **B. Strict isolation with no cross-workspace access at all.** Honest
  but inconvenient: a chat opened in `work/` parent could not read any
  project's memory, and the UI could only ever show one workspace at a
  time even though the user often wants to compare.

- **C. Hub mode + workspace registry.** One HTTP service serves many
  workspaces through a registry that maps `workspace_id` to a physical
  DB path. The MCP stdio server already uses HTTP delegation for heavy
  reads/writes (so the embedding model stays warm in one process); the
  hub model lets that delegation route per-call by sending
  `X-Memory-DB-Path` headers resolved from the registry.

## Decision

Adopt option C. The registry lives at
`~/.agent_memory/workspaces.json` (override via `MEMORY_WORKSPACES_FILE`)
and is updated automatically by every `setup_agent.py --project` call.
Every entry stores `(workspace_id, db_path, vector_path, project_root,
label, registered_at, last_seen_at)`.

The HTTP service has two operating modes:

- **Strict (single-anchor) mode.** Legacy behavior. Set
  `MEMORY_HUB_MODE=false` or run `serve.py --strict`. The service
  rejects any `workspace_id` that does not match its env-pinned
  `MEMORY_WORKSPACE_ID`.

- **Hub mode.** `MEMORY_HUB_MODE=true` (or registry has at least one
  entry, which `serve.py` treats as the default). The strict guard is
  off; per-request `X-Memory-DB-Path` (header) or `db_path` (query
  param, used by SSE) is the boundary.

The MCP stdio server reads the registry on startup. If the registry has
multiple entries and no explicit project env is set, the server
auto-enables hub mode and uses the first entry as a safe-but-empty
anchor. Per-call HTTP delegation looks up the request's `workspace_id`
in the registry and uses that entry's `db_path`/`vector_path` for the
`X-Memory-DB-Path` headers, so the right physical DB is reached without
any project-specific MCP env.

Three new endpoints expose the registry over HTTP:

- `GET /memory/workspaces` returns hub_mode + the full registered list.
- `POST /memory/workspaces` registers/updates an entry.
- `DELETE /memory/workspaces/{workspace_id}` removes an entry (does not
  touch SQLite/Lance data).

A small CLI `scripts/register_workspace.py` (subcommands: `list`,
`register`, `remove`) lets the user inspect or repair the registry
without HTTP.

The local UI subscribes to the registry: the `Workspace` dropdown is
populated from `/memory/ui/state` (which now returns
`registered_workspaces`), and switching the dropdown sends
`X-Memory-DB-Path` / `X-Memory-Vector-Path` on subsequent fetches and
appends `?db_path=...&vector_path=...` on the EventSource SSE URL
(EventSource cannot set custom headers).

## Consequences

Positive:

- One HTTP service handles many projects. No per-project ports.
- Single UI dropdown switches between project memories live.
- Auto-routing means a global `UserPromptSubmit` hook (no
  `--workspace` flag) resolves the right DB from `cwd` via the registry
  and stops emitting confusing 400 errors.
- New projects just call `setup_agent.py --project` once; everything
  else (UI, hub service, hooks) discovers them automatically.

Negative:

- The registry is a single point that links every project's DB path on
  the machine. A buggy `register_workspace.py register` could mis-route
  a write. Mitigation: per-call `X-Memory-DB-Path` is still the actual
  isolation boundary; the registry is only a router, not a permission
  store.

- An MCP server in hub mode can read any registered workspace. We
  separated reads from writes so this doesn't violate per-project
  trust — see ADR 0005.

- `~/.agent_memory/` is now a developer-machine concern that isn't
  guarded by `.gitignore` of any project. Documented in
  `register_workspace.py` and `AGENT_CONTRACT.md`.
