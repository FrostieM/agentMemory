# Code-memory operator guide

This document is the operator's reference for the code-memory
substrate built across v1.4 → v2.1.x. It covers what each tool
does, when to use which, the env flags that gate behavior, and
the typical workflows ("agent claim before editing", "ship-time
breaking-change check", etc.) that compose them.

Read this AFTER you've shipped v2.1.5 and have at least one
project's worth of code ingested. Without ingested code, all the
endpoints below return empty.

> **v3.0.0 — start with `memory_impact_check`.** The version-
> current discipline primitive replaces a 3-call sequence
> (`memory_file_digest` + `memory_graph_neighbors` + ad-hoc Grep)
> with one envelope:  digest + callers + hot_symbols + verdict +
> advisory. Returns `verdict ∈ {not_indexed, low, medium, high}`
> and a one-sentence advisory naming the right follow-up tool.
> Pinned as the workspace rule **graph-tools-first** via
> `scripts/seed_memory_discipline.py`. See
> [`V3_AGENT_RUNTIMES.md`](V3_AGENT_RUNTIMES.md) §"Tool name
> reference". The v1.4-2.1.x tools below remain available for
> deeper drilldowns once impact_check identifies the right hot
> symbol.

---

## What was shipped — five layers

```
            ┌────────────────────────────────────┐
            │  v2.0  Dashboard (UI + JSON)       │   /ui/code, /ui/graph
            ├────────────────────────────────────┤
            │  v1.8  Narrative file digests      │   /memory/file_digest
            ├────────────────────────────────────┤
            │  v1.7  Soft graph + edit registry  │   soft_neighbors / claim_edit
            ├────────────────────────────────────┤
            │  v1.6  Versioning + breaking      │   symbol_history / breaking_changes
            ├────────────────────────────────────┤
            │  v1.5  Hard graph (edges)          │   graph_neighbors
            ├────────────────────────────────────┤
            │  v1.4  Symbol-level chunks         │   find_symbols
            └────────────────────────────────────┘
```

Higher layers depend on lower layers. To populate everything,
ingest a code file (e.g. `POST /memory/ingest_file` with
`language="python"`); the pipeline writes chunks (v1.4), edges
(v1.5), versions (v1.6), soft edges + active-edit substrate
(v1.7), file digest (v1.8), and the dashboard reflects all of it
(v2.0).

---

## Tool index — what to call when

### "Find a specific function or class"

`memory_find_symbols(name="paperBot.calculate")` (v1.4.0)
or prefix: `memory_find_symbols(name_prefix="paperBot.")`.

Returns the chunk body directly; no FTS needed.

### "Who depends on X?" / "What does X depend on?"

`memory_graph_neighbors(qualified_name="paperBot.calculate", direction="upstream")` (v1.5.0)
returns the inbound CALLS / IMPORTS / EXTENDS edges.

`memory_graph_neighbors(chunk_id=..., direction="downstream")` returns outbound.

For non-Python languages, edges include `extends` /
`implements` / `decorated_by` after v2.1.1 — coverage matrix in
the v2.1.1 changelog entry.

### "Who could break after my refactor?"

`memory_breaking_changes(since_days=7)` (v1.6.0). Lists every
symbol whose signature_hash changed in the window, with prev/new
diff and downstream caller_count via the hard graph. Use right
before a release.

`memory_symbol_history(qualified_name="paperBot.calculate")`
shows the full version chain for one symbol.

### "Multi-agent coordination — who's editing what?"

Before starting work on a target:

```
memory_list_active_edits(workspace_id=...)
memory_claim_edit(qualified_name="paperBot.calculate", agent_id="claude", ttl_minutes=30)
```

If another agent already has the claim, `claim_edit` returns
`{claimed: false, blocked_by: <agent>, blocked_until: <iso>}`.

After you finish:

```
memory_release_edit(claim_id=...)
```

### "Find similar code"

`memory_soft_neighbors(src_qualified_name="fetch_users", edge_kinds=["similar_signature"])`
(v2.1.4). Returns symbols whose MinHash Jaccard ≥ threshold.
Use when refactoring `fetch_users` and want to apply the same
pattern to `fetch_orders`, `fetch_products`.

`edge_kinds=["co_changed"]` returns symbols that historically
change in the same ingest pass — a complementary signal when
the hard graph misses an explicit dependency.

### "Workspace overview / dashboard"

`memory_code_overview(workspace_id=...)` (v2.0) — single read
returns counts, recent files, breaking changes, active edits,
top-called symbols. Foundation for `/ui/code`.

Browser: open `http://127.0.0.1:8765/ui/code` for the table view
and `http://127.0.0.1:8765/ui/graph` (v2.1.2) for the D3
force-directed graph.

### "What does this file do?"

`memory_file_digest(file_path="src/m.py")` (v1.8.0). Returns the
narrative + structured metadata (chunk count, symbol kinds,
edge counts, recent versions).

When `MEMORY_LLM_NARRATIVE_ENABLED=true` (v2.1.3), the narrative
is a 2-3 sentence Ollama-generated paragraph; otherwise it's the
heuristic baseline ("Contains: 1 class, 2 functions. Edges:
5 inbound."). The `structured.narrative_source` field tells you
which path produced the text.

---

## Typical workflows

### Workflow 1 — onboarding to an unfamiliar workspace

1. `memory_code_overview(workspace_id=X)` to see counts +
   recent_files.
2. Open `/ui/graph?workspace_id=X` in a browser to inspect
   topology.
3. For interesting clusters, double-click a node to re-center the
   BFS.
4. For specific symbols of interest:
   `memory_get_object(kind="...", id="...")` for the full body or
   `memory_find_symbols(name=...)` for the chunk text.

### Workflow 2 — agent claims before editing

1. `memory_list_active_edits(workspace_id=X)` — see who else is
   working.
2. `memory_claim_edit(qualified_name=..., agent_id=...)` — claim
   the target. If 409, coordinate with the blocking agent.
3. Edit the file.
4. `memory_ingest_file(workspace_id=X, path=..., content=..., language=...)`
   — re-ingest so chunks / edges / versions / digest update.
5. `memory_release_edit(claim_id=...)` — release the claim.

### Workflow 3 — pre-release breaking-change scan

1. `memory_breaking_changes(workspace_id=X, since_days=7)` — list
   signature changes in the last week.
2. For each result, look at `caller_count`. ≥1 means there's at
   least one downstream caller of the changed symbol.
3. For high-impact changes, drill into
   `memory_graph_neighbors(qualified_name=..., direction="upstream", edge_kinds=["calls", "instantiates"])`
   to enumerate the actual callers.
4. Validate the callers compile / pass tests with the new
   signature.

### Workflow 4 — refactor pattern propagation

1. After changing `fetch_users(...) -> list[User]`, ask
   `memory_soft_neighbors(src_qualified_name="fetch_users", edge_kinds=["similar_signature"])`.
2. Returned symbols (`fetch_orders`, `fetch_products`) probably
   need the same change. Apply it.
3. Re-ingest each touched file; `similar_signature` weights
   accumulate, future suggestions get stronger.

---

## Env flags — when to flip

Defaults are calibrated for typical use. Change only with reason.

### Code-memory specific (v2.1.x)

* ``MEMORY_LLM_NARRATIVE_ENABLED`` — default false. Flip to true
  when Ollama is healthy and you want richer file digests.
  Adds ~10s/file at ingest time (timeout-bounded).
* ``MEMORY_LLM_NARRATIVE_MIN_SYMBOLS`` (default 3) — skip LLM
  call for trivial files.
* ``MEMORY_LLM_NARRATIVE_TIMEOUT_SEC`` (default 10) — per-file
  Ollama call timeout.
* ``MEMORY_LLM_NARRATIVE_MAX_INPUT_CHARS`` (default 4000) — cap
  the prompt size.
* ``MEMORY_SIMILAR_SIG_ENABLED`` — default true. Cost is bounded
  by SCAN_LIMIT, so leave on unless you're seeing noise.
* ``MEMORY_SIMILAR_SIG_THRESHOLD`` (default 0.7) — Jaccard
  cutoff. Lower = more edges (and more noise). Calibrate per
  workspace once you have data.
* ``MEMORY_SIMILAR_SIG_SCAN_LIMIT`` (default 200) — candidate
  pool size. Increase for sparsely-connected workspaces; keep
  default for typical codebases.

### Earlier env flags

For hygiene / feedback / behavior settings (v1.0–v1.10), see
`docs/V1_1_0.md`. Code-memory flags above are NEW in v2.1.x.

---

## Backstops

* **Empty workspace** — every endpoint returns empty arrays
  cleanly. The dashboard shows "(no graph data for workspace)".
  Fix: ingest at least one code file.
* **Ollama unreachable with LLM narrative on** — falls back to
  heuristic narrative; ``structured.narrative_source`` flips to
  ``"heuristic"``. No errors.
* **Tree-sitter grammar missing for a language** — chunking
  falls back to token-window split (no qualified_name); edges
  fall back to empty list. Service stays usable.
* **Multi-agent claim conflict** — `claim_edit` returns 409 (or
  `claimed=false` from MCP). No silent overwrite.

## When in doubt

`memory_explain_context(query=...)` shows what the retrieval
pipeline returned and why. `memory_list_audit(target_type=..., target_id=...)`
shows the per-row write history. Both are read-only; safe to call
liberally.
