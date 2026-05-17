# Operations guide

Operational knowledge for running agent-memory-lite day-to-day:
upgrade workflow, service auto-start, hook fallback chain, hub-mode +
legacy DB behaviour, troubleshooting common failure modes. Pair with
[`docs/V1_1_0.md`](V1_1_0.md) for the env-flag map and
[`docs/V1_1_0_CALIBRATION.md`](V1_1_0_CALIBRATION.md) for calibration
evidence.

## v3.0.0 deployment (canonical path)

One command sets up everything on a new project:

```bash
python scripts/setup_agent.py --project /path/to/your/project
```

This:

1. Bootstraps the project's SQLite + LanceDB pair under
   `<project>/.agent_memory/`.
2. Registers the workspace in `~/.agent_memory/workspaces.json` so the
   hub mode can route to it.
3. Applies the v3 schema (idempotent — `CREATE TABLE IF NOT EXISTS`
   for every table; safe on existing v2 DBs).
4. Seeds the 3 pinned discipline rules (graph-tools-first /
   search-before-write / capability-link-on-write) into the v3
   `behaviors` table.
5. Writes hooks to `<project>/.claude/settings.json`:
   * `UserPromptSubmit` → `inject_memory_brief_v3.py`
     (≤500-token brief)
   * `PostToolUse` (`Edit|Write|NotebookEdit|MultiEdit`) →
     `post_edit_enqueue.py` (digest queue)
   * `PreToolUse` enforcement (existing v2 path, unchanged)
6. Updates project `CLAUDE.md` + `AGENTS.md` with the agent contract.

Idempotent — re-running is safe. New rules / hooks insert; existing
ones are detected by marker substring and refreshed.

### Verify deployment

After `setup_agent.py` finishes:

```bash
# 1. Settings.json has both v3 hooks
python -c "import json; d = json.load(open('<project>/.claude/settings.json')); \
  print('UserPromptSubmit:', any('v3-brief' in str(h) for h in d.get('hooks', {}).get('UserPromptSubmit', []))); \
  print('PostToolUse:',     any('v3-postedit' in str(h) for h in d.get('hooks', {}).get('PostToolUse', [])))"

# 2. Pinned rules in DB
sqlite3 <project>/.agent_memory/memory.db \
  "SELECT name, pinned FROM behaviors WHERE pinned=1 ORDER BY name;"

# 3. Compose a brief manually (no Claude Code session needed)
python -c "import sqlite3; from agent_memory_lite.v3.cognition.brief import compose_brief; \
  conn = sqlite3.connect('<project>/.agent_memory/memory.db'); \
  print(compose_brief(conn, workspace_id='<your-workspace>').body_md)"
```

After 1-2 days of work in the project, measure adoption:

```bash
python scripts/measure_tool_usage.py --since-days 2
```

Target: `graph_share >= 0.30` — agent reaches for `memory_v3_*`
instead of `Read` / `Grep`. Baseline measured on existing projects:
`0.00%` (graph tools effectively unused before v3 stack).

### Restart what

After installing v3 hooks, the agent runtime needs a refresh to pick
them up:

* **Claude Code chats**: open a **new** session in the project root —
  `.claude/settings.json` is read at session start, current chats
  don't re-read it mid-flight.
* **MCP stdio server**: spawned per chat — new chat = new MCP =
  fresh v3 handler code.
* **HTTP service** on port 8765: rebuild only needed if you want
  `/v3/memory/*` routes (mounted via `api/app_routes.py`). The
  hook-based brief/discipline stack doesn't go through HTTP.

### v3-only installer (advanced)

`scripts/install_v3_hooks.py` is the lower-level installer used
internally by `setup_agent.py`. Useful when you only want to refresh
hooks without re-running the full `setup_agent.py` flow:

```bash
# Dry-run (default):
python scripts/install_v3_hooks.py --project /path/to/project

# Apply:
python scripts/install_v3_hooks.py --project /path/to/project --apply --backup-first

# Hooks only, skip seed:
python scripts/install_v3_hooks.py --project /path/to/project --apply --no-seed
```

## After upgrading the repo

After `git pull` / a new tag, three things may need refreshing:

1. **MCP server processes are long-lived.** Claude Desktop / Cursor /
   VS Code spawn the MCP stdio server once per session and keep the
   Python module imported in memory. Old processes spawned before
   the upgrade keep running the old code. Restart the IDE (full
   quit, not just close window) to pick up new code.

2. **HTTP service** at `127.0.0.1:8765` doesn't auto-restart when the
   repo updates. Stop and start it:

   ```powershell
   # PowerShell
   Get-NetTCPConnection -LocalPort 8765 -State Listen `
     | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
   # then your usual start: Task Scheduler / Startup script /
   # `python -m agent_memory_lite`
   ```

   If you installed the Task Scheduler service (see below), the next
   logon picks up the new code automatically.

3. **DB migrations apply on the first connection.** Forward-only,
   automatic via `db/migrations.py:apply_migrations`. Verify:

   ```bash
   curl -s http://127.0.0.1:8765/health \
     | python -c "import sys,json; d=json.load(sys.stdin); print(d['version'], d['applied_migrations'][-1])"
   # expected: 1.1.0  0025_hygiene_recurrence
   ```

## Service auto-start options

Three options for keeping the HTTP service alive on a developer
machine. Pick one — running multiple competes for port 8765.

### Task Scheduler — production grade (Windows, requires admin once)

* Auto-starts at login.
* `RestartCount=3` with `RestartInterval=1 minute` — automatic
  restart on crash.
* No interactive console; logs go to `logs/`.

Install (one-time, requires admin elevation):

```powershell
# Run as Administrator:
powershell -ExecutionPolicy Bypass -File `
  "C:\path\to\agent-memory-lite\scripts\memory_service_task.ps1" `
  -Action Install `
  -WorkspaceId <your-workspace-id> `
  -ProjectRoot "C:\path\to\agent-memory-lite" `
  -HubMode
```

Health check (read-only, no admin):

```powershell
powershell -ExecutionPolicy Bypass -File `
  "C:\path\to\agent-memory-lite\scripts\memory_service_health.ps1" `
  -WorkspaceId <your-workspace-id>
```

Uninstall:

```powershell
# Run as Administrator:
powershell -ExecutionPolicy Bypass -File `
  "C:\path\to\agent-memory-lite\scripts\memory_service_task.ps1" `
  -Action Uninstall -WorkspaceId <your-workspace-id>
```

### Startup folder — dev grade (Windows, no admin)

Drop a small launcher in the user Startup folder. Auto-starts at
login but **no restart on crash** — if the process dies, it stays
dead until next login.

```powershell
$startupDir = [Environment]::GetFolderPath('Startup')
$scriptPath = Join-Path $startupDir "agent-memory-lite.ps1"
$repo = "C:\path\to\agent-memory-lite"
$pyExe = Join-Path $repo ".venv\Scripts\python.exe"
$logPath = Join-Path $repo "logs\agent-memory-service.log"
$content = @"
`$env:MEMORY_WORKSPACE_ID = '<your-workspace-id>'
Set-Location -LiteralPath '$repo'
Start-Process -FilePath '$pyExe' -ArgumentList '-m','agent_memory_lite' \``
    -WorkingDirectory '$repo' -WindowStyle Hidden \``
    -RedirectStandardOutput '$logPath' \``
    -RedirectStandardError '$logPath.err'
"@
Set-Content -LiteralPath $scriptPath -Value $content -Encoding UTF8
```

Remove later:

```powershell
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\agent-memory-lite.ps1"
```

### Manual — debug / CI

Just run from terminal. Stays alive while terminal is open.

```bash
cd /path/to/agent-memory-lite
.venv/Scripts/python -m agent_memory_lite
# or in a separate terminal for hub mode:
.venv/Scripts/python scripts/serve.py --hub
```

### Switching between options

If you have a Startup folder script and want to switch to Task
Scheduler, **remove the Startup script first** before installing the
task — otherwise both try to bind port 8765 at login and one of them
fails noisily.

```powershell
# kill running service
Get-NetTCPConnection -LocalPort 8765 -State Listen `
  | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
# remove startup script
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\agent-memory-lite.ps1" -ErrorAction SilentlyContinue
# install scheduled task (run from admin shell)
# ... see Task Scheduler section above
Start-ScheduledTask -TaskName "agent-memory-lite-<your-workspace-id>"
```

## Hook fallback chain

`scripts/inject_memory_context.py` runs on every user prompt
(UserPromptSubmit hook). The fallback order is:

```
1. POST http://127.0.0.1:8765/memory/get_context (default path)
   ↓ on httpx.ConnectError (service down / refused)
2. Open SQLite directly + run FTS-only build (~30ms, no embedding)
   ↓ on import or DB error
3. Emit notice in <agent-memory> tag — agent runs blind on memory
```

Step 2 (FTS fallback) was added in 1.1.0. Quality is degraded vs the
HTTP path: no vector ranking, no graph walk, no EWMA re-rank. But
`<core_memory>` / `<behavior_instructions>` / `<active_decisions>` /
`<retrieved_chunks>` still render — agent is not blind.

When the hook lands at step 3, fix the service (see auto-start
section). Step 2 is a resilience layer, not a substitute.

## Hub mode + legacy DBs

The HTTP service runs in **hub mode** when `MEMORY_HUB_MODE=true` or
when `~/.agent_memory/workspaces.json` has more than one entry.
In hub mode, each request is routed per-call via the
`X-Memory-DB-Path` header; the same service can serve multiple
projects from one process.

The `UserPromptSubmit` hook auto-routes by walking up from `cwd`
looking for a registered project root in `workspaces.json`. If
nothing matches (e.g. you opened an admin shell in
`C:\WINDOWS\system32` or any other un-registered directory), the
hook falls back to a global workspace at `~/.agent_memory/global/`
(opt out with `AGENT_MEMORY_HOOK_FALLBACK=disabled`).

**The catch:** that global DB might have been bootstrapped by an
older release with only v1.0.x migrations applied. When the v1.1.0
service routes a request to it, the v1.5 / v1.6 / v2.2 post-build
hooks try to write columns / tables that don't exist on the legacy
schema.

1.1.0 handles this gracefully: every legacy-schema write path catches
`sqlite3.OperationalError` and degrades to a no-op. The route still
returns 200. The catch-all global DB is read-mostly anyway so the
silent skip is fine.

If you want the legacy DB upgraded:

```bash
# Either: open it with the current service once — apply_migrations
# auto-applies on first connection.
# Or: re-bootstrap explicitly:
python scripts/setup_agent.py --project ~/.agent_memory/global \
  --workspace global --no-hook
```

## Troubleshooting

### `<agent-memory>` notice: "agent-memory-lite is not running"

The HTTP service is down. The hook tried the FTS fallback and that
also failed (no DB resolved from cwd / registry). Either start the
service (see auto-start), or check that your project root is in
`~/.agent_memory/workspaces.json`:

```bash
python scripts/register_workspace.py list
python scripts/register_workspace.py register \
  --workspace <id> --project /path/to/your/project
```

### `<agent-memory>` notice: "agent-memory-lite returned HTTP 500"

Look at the service log for the actual exception. Two common causes:

* **Legacy DB** routed via hub mode — fixed in 1.1.0; if you're on an
  older version, upgrade or set `MEMORY_HUB_MODE=false` and pin to
  one workspace.
* **Pollution / corruption** — run
  `python scripts/memory_audit.py --workspace <id> --json`
  to inspect; repair only with explicit `--repair-*` flags after
  reading the report.

### MCP tools work, hook doesn't

The hook depends on HTTP (with FTS fallback). MCP stdio is
independent — has its own local fallback. If MCP works but the hook
shows "not running", the FTS fallback path also failed. Most likely
cause: the cwd isn't registered AND
`AGENT_MEMORY_HOOK_FALLBACK=disabled` was set, so the hook didn't
auto-bootstrap the global workspace. Either register your cwd, or
clear the env var.

### `no such table` / `no such column` in service logs

Means a legacy-schema DB got routed to a v1.1.0 code path. As of
1.1.0 these errors no longer surface as 500 — the hook just no-ops
the affected feature. To recover full functionality on that DB, run
`apply_migrations` on it (see legacy DB section above).

### Port 8765 already in use at startup

Two service instances trying to bind. Common causes:

* Both Task Scheduler and Startup folder script installed — pick one.
* Manual `python -m agent_memory_lite` left running. Find + kill:
  ```powershell
  Get-NetTCPConnection -LocalPort 8765 -State Listen `
    | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
  ```

### `workspace_pollution` warning in `/health`

Some other workspace_id has rows in your DB. Inspect:

```bash
python scripts/memory_workspace_doctor.py --workspace <id> --json
```

Quarantine only after review with `--quarantine --backup-first`.
This exports foreign rows to JSON before deletion.

## Workspace lifecycle

### Register a new project

`setup_agent.py --project` does the full setup: copies the agent
contract into project's `CLAUDE.md` / `AGENTS.md`, writes
`.claude/settings.json` with MCP entry + hook command, bootstraps
`.agent_memory/`, registers in `~/.agent_memory/workspaces.json`.

```bash
# From the agent-memory-lite repo:
python scripts/setup_agent.py --project /path/to/your/project \
  --workspace <id> --yes
```

### Switch the running session to a different workspace

Two ways:

* **UI dropdown** at `http://127.0.0.1:8765/ui` — top-right corner,
  picks any registered workspace, no service restart.
* **MCP per-call routing** — pass `workspace_id` in the call (and
  the hub-mode service routes to that DB via the registry).

### Remove a workspace

```bash
python scripts/register_workspace.py remove --workspace <id>
```

The `.agent_memory/` directory stays (data preserved). Delete
manually if you want full cleanup.

## See also

* [`docs/V1_1_0.md`](V1_1_0.md) — env-flag map for all the v1.4-v1.9 + v2 loops
* [`docs/V1_1_0_CALIBRATION.md`](V1_1_0_CALIBRATION.md) — calibration evidence + reproduction
* [`docs/AGENT_CONTRACT.md`](AGENT_CONTRACT.md) — canonical agent operating contract
* [`CHANGELOG.md`](../CHANGELOG.md) — release notes
