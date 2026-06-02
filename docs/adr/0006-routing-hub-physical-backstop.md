# ADR 0006: Routing hub with a physical-file backstop and anchor integrity

Status: accepted (2026-06-02)

Supersedes the mental model of ADR-era "path is the isolation primitive";
builds on ADR 0004 (hub mode) and ADR 0005 (asymmetric read/write isolation),
both of which remain in force.

## Context

ADR 0005 made writes strict and reads loose, enforced by
`ensure_workspace_writable`. That guard answers "is this agent *allowed* to
write workspace_id X?" It does NOT answer "did this write physically land in
X's own database?" Two real failures showed the gap:

1. **The 2026-05-21 leak.** 134 copyBot `ingest_file` writes landed in the
   agent-memory-lite SQLite file. The write guard did not catch it: in hub mode
   the guard permits every workspace_id by design, and with strict isolation
   off it permits foreign writes too. The HTTP layer had a physical backstop
   (`ensure_workspace_matches_db`) but the **MCP write path had none** -- a
   mis-routed connection wrote silently into the anchor DB.

2. **Mis-anchoring pain.** A chat launched under a parent directory whose
   `.mcp.json` pinned another project's `MEMORY_DB_PATH` (e.g. every chat under
   `work/` inheriting a `copyBot` pin) anchored to the wrong workspace. Its own
   writes were then *rejected* by strict isolation ("expected copyBot"), and a
   hub should route memory to the right place, not block one workspace because
   the server is anchored to another.

The unifying lesson: **the physical path is one isolation layer, not the
isolation primitive.** A single mis-anchored process makes two workspaces share
one file, which the path-separation argument assumed impossible.

## Decision

Treat the memory service as a routing **hub** and enforce isolation with
defense-in-depth on every write, so a misroute fails closed instead of
silently polluting.

1. **Anchor by cwd, verify at startup.** `resolve_paths_from_cwd` anchors each
   MCP server to the workspace whose registered `project_root` contains the
   cwd (path-normalized via `os.path.samefile` + resolved compare, so symlink /
   subst / case / separator skew still matches). `assert_anchor_consistent`,
   called in `stdio_server._run()` before serving, REFUSES to start when the
   anchor `workspace_id` is registered but pinned to a different DB, or is
   unregistered but sitting on another registered workspace's DB. Fail-closed
   beats a silently mis-anchored process.

2. **Route by workspace_id.** Writes resolve the target workspace's own
   SQLite (`db_for`) and LanceDB (`store_for`) pair; `db_for`/`store_for` share
   one `.resolve()`-based path-equality so the SQL row and the episode vectors
   can never disagree about which workspace is the anchor. The hub directs
   memory to the right database; it does not block.

3. **Physical-file backstop on EVERY write path.** All four MCP write handlers
   (`write`, `edit`, `pin`, `archive`) and the HTTP write routes run
   `ensure_workspace_matches_db(conn, workspace_id, settings)` UNCONDITIONALLY
   (anchor and foreign writes alike). The episode branch additionally runs
   `ensure_store_matches_workspace(store, workspace_id, settings)` for the
   LanceDB side. Both reject -- with a canonical, path-free error envelope --
   when the routed connection / store is not the workspace's registered DB,
   before any row or vector is written. A per-DB workspace manifest is the
   fourth backstop.

4. **No shared "default" workspace primitive.** `forbid_default_workspace=true`
   in project mode; a bare/unregistered context is fail-closed rather than
   defaulting into a shared DB.

Cross-workspace writes remain an explicit `hub_mode` opt-in (ADR 0004) and the
read/write asymmetry is unchanged (ADR 0005); this ADR adds the physical and
anchor-integrity layers underneath them.

## Consequences

Positive:

- The 2026-05-21 misroute leak class is closed on the MCP path, not just HTTP.
  A cross-workspace (or mis-anchored anchor) write that does not physically
  match the registry is rejected before it lands.
- A mis-anchored server fails closed at boot with an actionable message instead
  of blocking its own project's writes or polluting another's.
- "Hub routes, does not block": a chat in its own (registered) project anchors
  correctly by cwd even when a parent `.mcp.json` is generic.

Negative / accepted:

- A parent `.mcp.json` must NOT pin a child workspace's `MEMORY_DB_PATH` /
  `MEMORY_WORKSPACE_ID`; doing so shadows sibling projects. Parent/shared
  contexts should be unpinned (cwd-routes) or an explicit hub.
- A bare, unregistered context (e.g. a chat in a parent dir that is not itself
  a registered workspace) is fail-closed: it starts but the first memory write
  errors until a workspace is chosen. This is intentional under "no default
  primitive".
- The backstop reads `LanceDBStore._db_path`; a real-store test pins that
  attribute so a future rename cannot silently disable the vector guard.
- Editable-install note: after these changes every already-running MCP/HTTP
  process must be RESTARTED to load the new cross-module import
  (`workspace_vector_path`); a stale process fails write calls with an
  ImportError until restarted.
