# V3 Migration Playbook

How to move a v2 workspace to v3 without data loss or downtime.

## Pre-flight

* **v2 DB stays untouched for 6 weeks** after cutover. Migration writes to a
  new `.agent_memory.v3-trial/memory.db` first; parity-verify, then promote.
* **Service can run both v2 and v3 surfaces simultaneously.** The v3 router
  mounts at `/memory/*`; v2 routes at `/memory/*` keep serving until you
  flip the workspace's `mode` flag.
* **MCP tools coexist.** v2 tool names (e.g. `memory_write_decision`) keep
  routing to the v2 backend; v3 tools (`memory_*`) use the v3 backend.
  The v2→v3 compat shim is OFF by default (`MEMORY_V2_COMPAT_ENABLED=false`)
  during the transition.

## Step-by-step

### 1. Snapshot the v2 workspace

```bash
python scripts/memory_audit.py --workspace <id> --json > pre_v3_audit.json
sqlite3 .agent_memory/memory.db ".backup .agent_memory/memory.db.bak"
```

### 2. Run the v2 → v3 port

```bash
python scripts/migrate_to_canonical.py \
  --source .agent_memory/memory.db \
  --target .agent_memory.v3-trial/memory.db \
  --workspace <id>
```

The script is idempotent and resumable: rerunning on a partial port picks up
where it left off. Output:

```
[migrate] applied schema_v3 (35 tables)
[migrate] copied 1247 decisions, 89 theories, 412 behaviors, ...
[migrate] computed gist columns for 1247 / 1247 decisions (heuristic)
[migrate] parity check: per-kind row counts match
[migrate] migration_report.json written
```

### 3. Verify parity

```bash
python scripts/migrate_to_canonical.py \
  --verify-only \
  --source .agent_memory/memory.db \
  --target .agent_memory.v3-trial/memory.db
```

Checks per-kind `COUNT(*)`. Any mismatch aborts and writes a diff report.

### 4. Validate gist quality (Phase 0 Ollama backfill)

```bash
# Sample 20 decisions; eyeball their auto-generated gists.
sqlite3 .agent_memory.v3-trial/memory.db \
  "SELECT title, gist FROM decisions ORDER BY RANDOM() LIMIT 20"
```

If the heuristic gist is too truncated or noisy, run the Ollama backfill:

```bash
python scripts/v3_gist_backfill.py \
  --db .agent_memory.v3-trial/memory.db \
  --workspace <id> --kind decision --batch 50
```

(The backfill script is added in week 2 of the v3 plan; if not present yet,
heuristic gists are good enough for cutover and Ollama upgrade follows.)

### 5. Promote to canary mode

Open `~/.agent_memory/workspaces.json` and add `"mode": "v3-shadow"` to the
workspace entry:

```jsonc
{
  "id": "agentLight",
  "db_path": ".agent_memory.v3-trial/memory.db",
  "vector_path": ".agent_memory.v3-trial/vectors.lance",
  "project_root": "/path/to/agentLight",
  "mode": "v3-shadow"
}
```

In shadow mode:

* v3 endpoints (`/memory/*`) and MCP `memory_*` tools route to the new DB.
* v2 endpoints continue routing to the original DB.
* You can probe v3 via `memory-cli` without touching production memory.

### 6. Run the cognition cron and digest worker

```powershell
# Windows:
.\scripts\memory_consolidation_task.ps1 -Action Install
```

```bash
# POSIX (manual run for now, scheduled task TBD):
python scripts/memory_consolidation_runner.py --json
```

Verify the digest queue is being consumed:

```bash
tail -f ~/.agent_memory/digest_queue.jsonl
```

### 7. Flip the canary

After 1-2 days of v3-shadow with no regressions:

```jsonc
{ ..., "mode": "v3" }
```

In `v3` mode the v2 HTTP routes return `410 Gone` and the v2 MCP tools route
through the compat shim (`MEMORY_V2_COMPAT_ENABLED=true`). The v2 DB stays on
disk but stops being written to.

### 8. Final cutover (week 8)

After all registered workspaces are on `mode: v3`:

* Drop the v2 HTTP routes (`api/routes/*` reduced from 83 → 8 files).
* Remove the v2 native MCP handlers; keep only the compat shim translation
  layer.
* Delete the original v2 DBs after 6-week retention period.
* Bump version to 3.0.0 final.

## Rollback procedure

If a v3-shadow probe reveals a problem:

1. Flip the workspace's `mode` back to `v2` in `workspaces.json`.
2. `memory-cli` and v3 endpoints become unavailable for that workspace.
3. v2 endpoints continue serving from the untouched v2 DB.

If a `v3` (no longer shadow) cutover reveals a problem within the 6-week
retention window:

1. Copy `.agent_memory.v3-trial/memory.db` to a backup location for forensics.
2. Restore from the `.agent_memory/memory.db.bak` taken in step 1 to the
   v2 DB path.
3. Flip `mode` back to `v2`.

After 6 weeks the v2 backup is purged; rollback requires re-migrating from the
v3 DB back to v2 (a script not yet built — the explicit assumption is that the
6-week canary window catches every problem).

## What's removed

See [`V3_REMOVED.md`](V3_REMOVED.md) for the kill-list (9,500 SLOC).

## See also

* [`V3_SCHEMA.md`](V3_SCHEMA.md) — table-by-table reference
* [`V3_AGENT_RUNTIMES.md`](V3_AGENT_RUNTIMES.md) — wiring per agent
