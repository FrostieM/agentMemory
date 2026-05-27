# V3 Migration Playbook

The active migration path is v3-only. There is no parallel canonical
directory, no Python v2-to-v3 porter, and no MCP compatibility shim.

## Current Path

1. Back up the workspace database.

```bash
python scripts/memory_audit.py --workspace <id> --json > pre_v3_audit.json
sqlite3 .agent_memory/memory.db ".backup .agent_memory/memory.db.bak"
```

2. Apply the canonical init migration.

```bash
python -m agent_memory_lite.db.migrations --db .agent_memory/memory.db
```

Fresh databases apply only `0001_init.sql`. It already contains the canonical
v3 tables, rebuilt foreign keys, and brain-loop tables.

The runner refuses to apply the squashed v3 init on top of a DB that still
contains old pre-v3 tables. Back up and rebootstrap instead of creating a
hybrid active schema.

3. Verify the workspace.

```bash
python scripts/memory_audit.py --workspace <id> --json
python scripts/memory_mcp_smoke.py --workspace <id> --require-behavior --require-capabilities --json
```

4. Use the compact v3 surface.

Agents should use `memory_brief`, `memory_search`, `memory_get`,
`memory_write`, `memory_edit`, `memory_pin`, `memory_archive`,
`memory_lint`, `memory_invoke_skill`, `memory_impact_check`,
`memory_status`, and `memory_plan`.

## Removed Paths

* `migrations/canonical/` was removed.
* Historical incremental migration files were squashed into `migrations/0001_init.sql`.
* `scripts/migrate_to_canonical.py` was removed.
* `mcp/v2_compat.py` was removed.
* v2 MCP tool names are not registered on stdio.
