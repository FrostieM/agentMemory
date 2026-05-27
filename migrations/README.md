# Migrations

Forward-only SQL migrations. The runner (`src/agent_memory_lite/db/migrations.py`)
discovers `migrations/NNNN_*.sql`, sorts by filename, and applies any not present in
the `schema_migrations` tracking table.

## Rules

1. **Forward-only.** No `down.sql`. If a migration is wrong, ship a *new* migration
   that fixes it (`0003_fix_xxx.sql`); never edit a migration that has already shipped.
2. **Recoverable.** Each `*.sql` file must be safe to re-run after a mid-script
   failure. The runner uses SQLite `executescript`; do not rely on rollback
   semantics for partially-created objects.
3. **Idempotent at the file level.** Use `CREATE TABLE IF NOT EXISTS`,
   `CREATE INDEX IF NOT EXISTS`, etc. Re-running on a partially-applied DB is a no-op.
4. **Filenames are immutable** once committed. The pattern is `NNNN_<slug>.sql`,
   four-digit numeric prefix, underscored slug.
5. **No PRAGMAs in migrations.** Connection-level PRAGMAs (`journal_mode`,
   `foreign_keys`, `synchronous`) live in `db/pragmas.py` and apply to every connection.
6. **Schema additions are safe; deletions are not.** Dropping a column or a table can
   strand application code on older versions. Coordinate any deletion as a multi-step
   migration: ship code that no longer reads the column, then ship the drop.

## Tracking table

The runner ensures this table exists before applying anything:

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
```

`version` is the migration filename without the `.sql` suffix
(e.g. `0001_init`).

## Escape hatch

Local DB only. If a migration corrupts state, delete `./.agent_memory/memory.db` and
re-ingest. Document any non-trivial recovery in `docs/runbook.md` (TBD).
