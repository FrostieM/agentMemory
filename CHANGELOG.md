# Changelog

All notable changes to agent-memory-lite. Versions follow semver — minor
bumps add functionality (and may flip a default), patch bumps fix bugs
without behaviour change.

## 2.1.2 — 2026-05-10

Second patch in the v2.1 polish series. Adds the **D3 graph
visualization** to the dashboard surface. Pre-2.1.2 ``/ui/code``
showed tables; tables are great for counts but bad for topology.
The new ``/ui/graph`` page renders an interactive force-directed
node-link graph backed by a new ``/memory/code_graph`` endpoint.

### What changed

- **`GET /memory/code_graph`** + **`memory_code_graph` MCP tool**.
  Two modes:
    - ``center`` set: BFS up to ``depth`` hops outward from one
      symbol, both directions, all edge kinds (or filter via
      ``edge_kinds[]``).
    - ``center`` absent: top-K most-connected symbols (overview).
  Cap via ``max_nodes`` (1-1000); response carries ``truncated``
  flag when the cap is hit.
- **`/ui/graph` HTML page** — D3.js v7 force-directed graph,
  loaded from CDN with integrity-checked SRI hash. Nodes colored
  by language (Python blue, Rust orange, TS deep-blue, etc.),
  sized by degree. Edge style varies by edge_kind (extends /
  implements bold blue, calls grey, imports dashed, decorated_by
  red, instantiates orange). Interactions: zoom + pan, click-
  drag, hover-tooltip, double-click to re-center the BFS.
- **Architecture**:
    - `api/routes/code_graph.py` — endpoint orchestration.
    - `api/routes/code_graph_bfs.py` — BFS + overview logic.
    - `api/routes/code_graph_bfs_sql.py` — SQL helpers split out
      to keep both modules under SLOC.
    - `api/routes/code_graph_models.py` — three pydantic types.
    - `mcp/tools_code_graph.py` + handler.
    - `ui/graph.html` — vanilla HTML+JS, no build step.

### Tests — 7 new

``tests/e2e/test_code_graph_route.py``:
1. Overview mode returns top-connected symbols.
2. Center mode BFS surfaces immediate neighbors.
3. ``max_nodes`` cap respected; ``truncated`` flag set.
4. Unknown ``edge_kinds`` value rejected with HTTP 400.
5. ``edge_kinds`` filter excludes other edge types.
6. Empty workspace returns empty arrays cleanly.
7. ``/ui/graph`` page is served, references ``/memory/code_graph``
   and includes the D3 CDN script tag.

Total tests now: **858 pass**.

### All gates green

- `ruff check` ✓
- `ruff format` ✓
- `mypy` (515 source files) ✓
- `check_sloc.py --enforce` ✓
- 858 tests pass.

## 2.1.1 — 2026-05-10

First patch in the v2.1 polish series (see
``docs/POST_V2_ROADMAP.md``). Closes the tree-sitter parity gap:
the 7 non-Python languages now emit ``extends``, ``implements``,
and ``decorated_by`` edges to match Python's stdlib-AST coverage.

### What changed

- **Per-language tables**
  (``extraction/symbol_edges_ts_decls.py``):
  - ``CLASSLIKE_NODES`` — class-like declaration node types
    whose heritage we walk for extends/implements edges.
  - ``DECORATOR_NODES`` — decorator / annotation / attribute
    node types per language.
- **Heritage extraction** (new module
  ``extraction/symbol_edges_ts_heritage.py``):
  - TypeScript: ``class_heritage`` → ``extends_clause`` /
    ``implements_clause``.
  - JavaScript: ``class_heritage`` → flat ``extends`` keyword
    + identifier (no clause wrapper, separate path).
  - Java: ``superclass`` + ``super_interfaces`` siblings;
    ``extends_interfaces`` for interface-to-interface inheritance.
  - C++: ``base_class_clause`` (all bases recorded as ``extends``
    since the syntax doesn't distinguish).
  - C#: ``base_list`` (same flat extends treatment).
  - Rust: ``impl Trait for Type`` recorded as ``implements`` from
    the type to the trait.
- **Decorator extraction** (new module
  ``extraction/symbol_edges_ts_decorators.py``): handles two
  patterns — children of the declaration (TS / Java / C#) and
  preceding siblings (Rust / C++).
- **Walker pass** (new module
  ``extraction/symbol_edges_ts_class_pass.py``): single tree
  traversal that emits all three new edge kinds, with dedupe
  via ``(src, dst, kind)`` key set. Decorator edges only
  attached to REAL declaration nodes (filtered against
  ``LANG_DECLS``) so a method-level decorator is NOT also
  attributed to the enclosing class.

### Coverage matrix

| Language    | extends | implements | decorated_by |
|-------------|---------|------------|--------------|
| javascript  | yes     | n/a        | n/a (stage-3 decorators rare) |
| typescript  | yes     | yes        | yes          |
| go          | n/a (composition) | n/a | n/a    |
| rust        | n/a (impl-only) | yes | yes      |
| java        | yes     | yes        | yes          |
| cpp         | yes     | n/a (no syntactic distinction) | yes |
| csharp      | yes     | n/a (no syntactic distinction) | yes |

### Tests — 8 new

``tests/unit/extraction/test_symbol_edges_ts_parity.py``:
1. JavaScript ``extends``.
2. TypeScript extends + implements + decorator (class + method).
3. Java extends + implements + ``@Override`` annotation.
4. C++ ``base_class_clause`` (multiple bases as ``extends``).
5. C# ``base_list`` + ``[Route]`` / ``[HttpGet]`` attributes.
6. Rust ``impl Trait for Type`` → ``implements`` + ``#[derive]``.
7. Class with NO heritage produces NO phantom extends.
8. Regression: method decorator stays on the method, not on
   the enclosing class.

Total tests now: **851 pass**.

### All gates green

- `ruff check` ✓
- `ruff format` ✓
- `mypy` (509 source files) ✓
- `check_sloc.py --enforce` ✓
- 851 tests pass.

## 2.0.0 — 2026-05-09

**v2.0 — code-memory dashboard.** The final phase of the V1.4→V2
roadmap. Six prior releases shipped the substrate (chunks, hard
graph, edges, versions, soft graph, active edits, file digests);
v2.0 collapses them into a single dashboard surface so an agent or
operator can answer "what's in this workspace, what's changing,
who's working on what" in one HTTP call or one browser tab.

### Headline

- **`GET /memory/code_overview`** + **`memory_code_overview` MCP
  tool**. Single read returns:
    - `counts` (files / chunks / symbols / edges / versions /
      soft_edges)
    - `recent_files` (file digests, newest-updated first, with
      narrative + edge counts)
    - `breaking` (signature-changed symbols in the last N days,
      with prev / new signature pairs)
    - `active_edits` (live edit claims by agent)
    - `top_called` (most-referenced symbols via inbound CALLS /
      INSTANTIATES edges)
- **`/ui/code` HTML dashboard** at the same hostname. Vanilla
  HTML+JS, no framework, no build step. Renders all five sections
  with a workspace selector and breaking-window slider; auto-loads
  on page open and refreshes on demand.
- Read-only end to end. Cleanup of expired active-edit claims
  happens server-side on each call so the view stays accurate.

### Tests — 6 new

- ``tests/e2e/test_code_overview_route.py``:
    1. empty workspace → zero counts, empty arrays.
    2. after ingest → file count + symbol count + at least one
       edge + at least 2 versions; ``helper`` shows up in
       ``top_called``.
    3. signature change → ``breaking`` section surfaces the diff.
    4. active edit claim → appears in ``active_edits``.
    5. ``files_limit`` parameter respected.
    6. ``/ui/code`` HTML page served, references the JSON URL.

Total tests now: **843 pass** (unit + e2e + integration + invariants).

### What v2.0 enables (the "team project" scenario)

The original ask was *"team project where multiple AIs work on the
same code; the memory should show conflicts, dependencies,
who-touches-what"*. v1.4-v1.8 built every data model that scenario
needs; v2.0 makes them visible in one place:

- **two agents diverge on the same function**: agent A's claim
  appears in `active_edits`, agent B sees it in the dashboard
  before starting.
- **someone changed `Foo.bar`'s signature**: `breaking` lists it
  with prev/new diff and downstream caller count via
  `graph_neighbors`.
- **operator wants the workspace overview**: `recent_files`
  shows the narrative for every code file, top-to-bottom by
  freshness.
- **agent wants "what depends on X"**: hard-graph
  `graph_neighbors` upstream lookup; soft-graph `soft_neighbors`
  for heuristic co-change relationships.

### Roadmap to v2.0 — DONE

| Version | Scope | Status |
|---------|-------|--------|
| 1.4.0   | Symbol chunks for 7 languages | shipped |
| 1.5.0–1.5.2 | Hard graph + tree-sitter edges + cross-file resolver | shipped |
| 1.6.0   | Symbol versioning + breaking-change detection | shipped |
| 1.7.0   | Soft graph + active-edit registry | shipped |
| 1.8.0   | Narrative file digests | shipped |
| **2.0.0** | **Code-memory dashboard** | **shipped** |

### All gates green

- `ruff check` ✓
- `ruff format` ✓
- `mypy` (506 source files) ✓
- `check_sloc.py --enforce` ✓
- 843 tests pass.

### Migration guidance

No schema migration required for v2.0 — the dashboard reads only
existing tables. Re-ingesting code files populates the file digests
that the dashboard depends on; existing chunks remain searchable
through `find_symbols` / `graph_neighbors` / `search`.

## 1.8.0 — 2026-05-09

Phase 5 of the V1.4→V2 code-memory roadmap: **narrative file
digests**. The last brick before v2.0's dashboard. Pre-1.8.0 the
agent had to enumerate chunks + edges + versions to build a "what
does this file do?" view from scratch every time. The digest is
that view — derived once at ingest time, persisted as a single
record per (workspace_id, file_path).

### Headline

- **`file_digests` table** (migration 0032). One row per file with:
  language, chunk_count, symbol_count, inbound_edge_count,
  outbound_edge_count, versions_recent (last 7 days), a
  human-readable ``narrative`` string, and a ``structured`` JSON
  field carrying the qualified-name list and per-kind tally.
- **Heuristic digest assembler**
  (``ingestion/file_digest_builder.py``). Pure derivation from the
  existing chunks / symbol_edges / symbol_versions tables —
  no LLM, no I/O beyond SQLite reads. The v1.8.x roadmap includes
  an optional Ollama enrichment pass; the heuristic baseline ships
  first because it works without external services.
- **Pipeline wiring**: digest is upserted on every file ingest
  pass as the final step in ``run_post_chunk_phase``. Idempotent
  by design — re-ingesting unchanged content overwrites the row
  with identical values, so history isn't churned.
- **`POST /memory/file_digest`** + **`memory_file_digest` MCP
  tool** — single file lookup. Returns 404 for files with no
  digest (e.g. non-code files).
- **`POST /memory/list_file_digests`** + **`memory_list_file_digests`
  MCP tool** — workspace overview, newest-updated first. The
  foundation for the v2.0 dashboard surface.

### Tests — 6 new

- ``tests/e2e/test_file_digests_routes.py``:
    1. digest_built_on_first_ingest — narrative + structured fields
       populated correctly.
    2. digest_updated_on_re_ingest — symbol_count tracks new symbols,
       updated_at advances.
    3. unknown_file_returns_404.
    4. list_digests_returns_workspace_overview — multi-file view.
    5. digest_edge_counts_reflect_graph — inbound/outbound counts
       reflect actual ``symbol_edges`` rows.
    6. digest_versions_recent_count — versions_recent ≥ 1 for a
       freshly-ingested file.

Total tests now: **837 pass** (unit + e2e + integration + invariants).

### Roadmap progress to v2.0

| Version | Scope | Status |
|---------|-------|--------|
| 1.4.0   | Symbol chunks for 7 languages | shipped |
| 1.5.0–1.5.2 | Hard graph + tree-sitter edges + cross-file resolver | shipped |
| 1.6.0   | Symbol versioning + breaking-change detection | shipped |
| 1.7.0   | Soft graph + active-edit registry | shipped |
| **1.8.0** | **Narrative file digests** | **shipped** |
| 2.0     | Dashboard surface | next + final |

### All gates green

- `ruff check` ✓
- `ruff format` ✓
- `mypy` (501 source files) ✓
- `check_sloc.py --enforce` ✓
- 837 tests pass.

## 1.7.0 — 2026-05-09

Phase 4 of the V1.4→V2 code-memory roadmap: **multi-agent
coordination layer** — soft graph + active-edit registry. Two
distinct features that together close the multi-agent code-
collaboration loop:

* **Active-edit registry** — short-lived TTL-bounded claims that one
  agent is currently editing a specific symbol or file. Other agents
  see the claim before starting work, avoiding clobbering.
* **Soft graph** — accumulated co-change / co-reference signals
  between symbols. The hard graph (``symbol_edges``) records EXPLICIT
  AST relationships; the soft graph captures HEURISTIC ones ("these
  symbols evolve together"). Weight accumulates per observation so
  noise filters naturally.

### Headline — active-edit registry

- **`active_edits` table** (migration 0031). Each row carries
  ``workspace_id``, ``qualified_name`` / ``file_path``, ``agent_id``,
  ``ttl`` (default 30 min), and ``note``. Lazy expiry: each
  read/write call cleans up expired rows for the workspace. No
  background worker.
- **`POST /memory/claim_edit`** + **`memory_claim_edit` MCP tool** —
  attempt to claim a target. Returns 200 with the claim row when
  successful; same agent can re-claim (idempotent extend); 409 when
  another agent already has an active claim. The MCP tool returns a
  graceful ``{"claimed": false, "blocked_by": "...", ...}`` rather
  than raising so the agent can branch on it.
- **`POST /memory/release_edit`** + **`memory_release_edit` MCP tool**
  — release a claim by ``claim_id``.
- **`POST /memory/list_active_edits`** + **`memory_list_active_edits`
  MCP tool** — read every non-expired claim. The starting agent's
  first move when entering a workspace.

### Headline — soft graph

- **`soft_edges` table** (migration 0031). Three edge kinds:
  ``co_changed``, ``co_referenced``, ``similar_signature`` (only
  ``co_changed`` is populated by the pipeline today; the others are
  schema-ready for v1.7.x extensions). UNIQUE index on
  ``(workspace_id, src, dst, edge_kind)`` so updates increment a
  weight rather than insert duplicates.
- **`ingestion/file_persist_soft_edges.py`** — co-change accumulator
  wired into the file ingest pipeline. After every chunk is
  persisted, every pair of qnames whose ``content_hash`` changed
  this pass receives a ``co_changed`` weight increment in both
  directions. ``record_versions_for_chunks`` now returns the list
  of changed qnames (it returned just a count before) so the
  accumulator knows which pairs to bump.
- **`POST /memory/soft_neighbors`** + **`memory_soft_neighbors` MCP
  tool** — weighted-and-ordered neighbor lookup. Use after the hard
  graph misses an edge you expected ("these two symbols feel like
  they should be related but don't have an explicit call site").

### Tests — 10 new

- ``tests/e2e/test_active_edits_routes.py`` (4 tests): claim →
  release round-trip, conflict 409 between two agents, idempotent
  re-claim by the same agent, missing-target 400.
- ``tests/e2e/test_soft_neighbors_route.py`` (6 tests): co-change
  pairs emitted on first ingest, weight accumulation across
  re-ingests, unrelated symbol returns empty, unknown kind 400,
  kind filter, ingest-side accumulator records pairs end-to-end.

Total tests now: **831 pass** (unit + e2e + integration + invariants).

### Roadmap progress to v2.0

| Version | Scope | Status |
|---------|-------|--------|
| 1.4.0   | Symbol chunks for 7 languages | shipped |
| 1.5.0   | Hard graph (Python edges) | shipped |
| 1.5.1   | Cross-file edge resolver | shipped |
| 1.5.2   | Tree-sitter edges for 7 languages | shipped |
| 1.6.0   | Symbol versioning + breaking-change detection | shipped |
| **1.7.0** | **Soft graph + active-edit registry** | **shipped** |
| 1.8.0   | Narrative file digests | next |
| 2.0     | Dashboard surface | final |

### All gates green

- `ruff check` ✓
- `ruff format` ✓
- `mypy` (494 source files) ✓
- `check_sloc.py --enforce` ✓
- 831 tests pass.

## 1.6.0 — 2026-05-09

Phase 3 of the V1.4→V2 code-memory roadmap: **symbol-level version
history + breaking-change detection**. v1.5.x answered "who calls
``paperBot.calculate``?" — v1.6.0 answers "the signature of
``paperBot.calculate`` just changed; who could break?". Together
they close the multi-agent code-coordination loop the README
headlines as the v2.0 deliverable.

### Headline — versioning

- **`symbol_versions` table** (migration 0030). Every chunk write
  appends a row when its content_hash differs from the most recent
  version for the same (workspace_id, qualified_name). The row
  carries the ``signature_text`` and ``content_hash`` inline so
  history stays visible even after the underlying chunk is deleted
  on file re-ingest.
- **Signature extractor**
  (``extraction/signature_extractor.py``). Best-effort first-line
  extraction across Python / TS / Rust / etc. — strips
  decorators, comments, docstrings; returns the line carrying
  ``def`` / ``class`` / ``fn`` / ``async fetch(...) {``. Hashed
  separately from content so a signature change is detectable
  without a body diff.
- **Pipeline wiring** (``ingestion/file_persist_versions.py`` +
  ``ingestion/file_post_chunk.py``). After every chunk is
  persisted, the version recorder runs idempotently — re-ingesting
  unchanged code never produces a new version row, so the history
  stays clean.

### Headline — breaking-change detection

- **`POST /memory/breaking_changes`** + **`memory_breaking_changes`
  MCP tool**. Lists every symbol whose signature_hash changed in
  the last N days, paired with downstream caller count via
  ``symbol_edges``. Use this right before a release: "who could
  break after my last refactor?". The query joins each version row
  to its immediately-prior version for the same (workspace_id,
  qualified_name) and filters for signature mismatches, then
  optionally counts inbound CALLS / INSTANTIATES edges per change.
- **`POST /memory/symbol_history`** + **`memory_symbol_history`
  MCP tool**. Full version chain for one symbol in descending
  chronological order; each row carries signature_text +
  content_hash captured at ingest time.
- **`IngestFileResponse`** now exposes ``versions_written`` so
  agents can verify the history is being recorded.

### Tests — 17 new

- ``tests/unit/extraction/test_signature_extractor.py`` (10 tests):
  per-language signature extraction edge cases — Python def,
  decorator skip, docstring skip, TS method, Rust attribute,
  hash stability, parameter-change detection.
- ``tests/e2e/test_versioning_routes.py`` (7 tests): full
  ingest → symbol_history / breaking_changes round-trip.
  Locks idempotence on unchanged content, body-only edits NOT
  appearing in breaking_changes (signature_hash unchanged), and
  caller_count via the hard graph for cross-file callers.

Total tests now: **821 pass** (unit + e2e + integration + invariants).

### Roadmap progress to v2.0

| Version | Scope | Status |
|---------|-------|--------|
| 1.4.0   | Symbol chunks for 7 languages | shipped |
| 1.5.0   | Hard graph (Python edges) | shipped |
| 1.5.1   | Cross-file edge resolver | shipped |
| 1.5.2   | Tree-sitter edges for 7 languages | shipped |
| **1.6.0** | **Symbol versioning + breaking-change detection** | **shipped** |
| 1.7.0   | Soft graph + active-edit registry | next |
| 1.8.0   | Narrative file digests | planned |
| 2.0     | Dashboard surface | final |

### All gates green

- `ruff check` ✓
- `ruff format` ✓
- `mypy` (484 source files) ✓
- `check_sloc.py --enforce` ✓
- 821 tests pass.

## 1.5.2 — 2026-05-09

Phase 2 completion: **hard-graph edges now extracted for all 7
non-Python languages** (JavaScript, TypeScript, Go, Rust, Java, C++,
C#) via tree-sitter. v1.5.0 shipped Python only; v1.5.2 brings every
supported language to feature parity for ``calls``, ``imports``, and
``instantiates`` edges. extends / decorated_by / implements remain
Python-only for now (extending the table-driven walker is a v1.5.3+
task).

### What changed

- New module ``extraction/symbol_edges_ts.py`` — tree-sitter-driven
  edge walker. Re-uses ``extract_symbols_via_ts`` from the chunking
  layer to identify function / method / class byte ranges, then
  walks the tree once more looking for call / import nodes. Every
  call site gets mapped to the smallest enclosing symbol so a call
  inside ``Service.fetch`` is owned by ``Service.fetch`` (not by
  the surrounding ``Service`` class).
- ``extraction/symbol_edges_ts_decls.py`` — per-language node-type
  tables (call expressions, import statements, instantiation
  variants). Java's ``method_invocation``, C#'s
  ``invocation_expression``, and C++'s ``preproc_include`` are all
  codified here so the walker stays generic.
- ``extraction/symbol_edges_ts_helpers.py`` — name extraction
  helpers. Handles strings (Go's ``interpreted_string_literal``,
  Rust's ``raw_string_literal``, C++'s ``system_lib_string``) and
  identifier-bearing nodes across grammar variants.
- ``ingestion/file_persist_edges.py`` dispatches by language:
  Python → ``extract_python_edges`` (stdlib AST, fastest);
  JS/TS/Go/Rust/Java/C++/C# → ``extract_ts_edges``. Falls back to
  empty list when grammars aren't installed — service stays usable
  without C extensions.
- ``IngestFileResponse`` (and matching MCP response) now carries
  ``edges_written`` so an agent can verify the graph populated.

### Tests — 11 new

- ``tests/unit/extraction/test_symbol_edges_ts.py`` (10 tests):
  per-language calls + imports coverage with skip-if-missing-grammar
  guards. Locks owner-resolution (calls inside method body owned by
  ``Class.method``), ``instantiates`` from ``new_expression``,
  language-specific string-literal handling for Go / C++ imports.
- ``tests/e2e/test_graph_neighbors_route.py::test_typescript_calls_emitted_via_tree_sitter``:
  full ingest → graph_neighbors round-trip on a TypeScript file.

Total tests now: **804 pass** (unit + e2e + integration + invariants).

### Roadmap progress to v2.0

| Version | Scope | Status |
|---------|-------|--------|
| 1.4.0   | Symbol chunks for 7 languages | shipped |
| 1.5.0   | Hard graph (Python edges) | shipped |
| 1.5.1   | Cross-file edge resolver | shipped |
| 1.5.2   | Tree-sitter edges for 7 languages | **shipped** |
| 1.6.0   | Symbol versioning + breaking-change detection | next |
| 1.7.0   | Soft graph + active-edit registry (multi-agent) | planned |
| 1.8.0   | Narrative file digests | planned |
| 2.0     | Dashboard surface | final |

### All gates green

- `ruff check` ✓
- `ruff format` ✓
- `mypy` (473 source files) ✓
- `check_sloc.py --enforce` ✓
- 804 tests pass.

## 1.5.1 — 2026-05-09

Patch follow-up to v1.5.0: **cross-file edge resolver**. Pre-1.5.1
an edge from file A to a symbol in file B started life with
``dst_chunk_id=NULL`` because B hadn't been ingested yet — and stayed
NULL forever, even after B's ingest. Now every file ingest runs a
resolver pass that links pending edges to the chunks that just
materialized.

### What changed

- New module ``repositories/symbol_edges_resolver.py`` —
  ``resolve_pending_edges_for_qnames()``. Two SQL match patterns per
  qname: exact (``dst_qualified_name = qname``) and dotted-suffix
  (``dst_qualified_name LIKE '%.qname'``) so ``from x import foo``
  imports stitch back to the bare ``foo`` chunk.
- ``ingestion/file_persist_edges.py`` calls the resolver after
  every ingest, even when the file's own body produces no edges
  (the chunks it wrote may still be the targets of earlier files'
  pending edges).
- Locked by a new e2e test
  (``tests/e2e/test_graph_neighbors_route.py::test_cross_file_import_resolves_after_target_ingested``):
  ingest A first → confirm edge has NULL dst_chunk_id → ingest B →
  confirm dst_chunk_id is now populated.

### All gates green

- `ruff check` ✓
- `ruff format` ✓
- `mypy` (470 source files) ✓
- `check_sloc.py --enforce` ✓
- 793 tests pass (unit + e2e + integration + invariants).

## 1.5.0 — 2026-05-09

Phase 2 of the V1.4→V2 code-memory roadmap: **hard-graph edges
between symbol chunks**. v1.4.0 made every function / class / method
its own indexable chunk; v1.5.0 connects them with explicit
relationship edges so an agent can ask "who calls `paperBot.calculate`?"
or "what does `Service.fetch` depend on?" without scanning every
chunk body for a substring.

### Headline — symbol edges

- **`symbol_edges` table** (migration 0029) with four partial
  indexes — fast O(log n) lookup by ``(workspace_id, src_chunk_id)``,
  ``(workspace_id, dst_qualified_name, edge_type)``, and reverse-
  direction indexes for prefix scans.
- **Edge types** (8): ``calls``, ``imports``, ``exports``,
  ``extends``, ``implements``, ``references``, ``instantiates``,
  ``decorated_by``. Validated at the extractor + repo boundary so
  a typo surfaces immediately.
- **Python AST extractor**
  (``extraction/symbol_edges_python.py`` + helpers). Walks each
  module body once and emits:
    - `calls`: every call site whose target name is resolvable
      (handles bare names, attribute chains like `self.helper`,
      module-qualified `mod.foo`).
    - `instantiates`: PascalCase calls (`MyClass(1, 2)`) get this
      kind instead of `calls` — heuristic but useful for
      "who instantiates MyClass?" lookups.
    - `imports`: module-level `import x` and `from x import y`,
      anchored to a synthetic `<module>` owner.
    - `extends`: every base class on a `class Foo(Base):` decl.
    - `decorated_by`: `@decorator` on functions, methods, classes.
- **Pipeline wiring** (`ingestion/file_persist_edges.py`): after
  every chunk is persisted, the edge extractor runs and resolves
  `src_chunk_id` from the freshly-built `qualified_name → chunk_id`
  map. `dst_chunk_id` is populated for within-file targets;
  external targets (stdlib, third-party imports, methods we haven't
  seen yet) leave `dst_chunk_id` NULL so a later resolver pass can
  fill them in as the workspace fills out.
- **Re-ingest cleanup**: when a file is re-ingested, the pipeline
  drops every edge whose `src_chunk_id` references an
  about-to-be-deleted chunk BEFORE the chunks themselves are
  deleted. Locked by an e2e regression test.

### Surface

- **`POST /memory/graph_neighbors`** + **`memory_graph_neighbors`
  MCP tool**. Pass `qualified_name` for upstream lookups (who
  depends on this symbol?), `chunk_id` for downstream lookups
  (what does this symbol depend on?), or both for a full
  neighborhood. Filter by `edge_types` and `direction`.

### Tests — 15 new

- `tests/unit/extraction/test_symbol_edges_python.py` (8 tests):
  per-edge-type extractor coverage — calls inside functions,
  method-call ownership, module-level imports, `extends` with
  simple/dotted/multi bases, decorators on functions and methods,
  PascalCase instantiation heuristic, unparseable input handling,
  module-level call skip behavior.
- `tests/e2e/test_graph_neighbors_route.py` (7 tests): full
  ingest → graph_neighbors round-trip — upstream by qualified_name,
  downstream by chunk_id, extends-edge upstream, imports-edge
  attached to `<module>`, unknown-edge-type rejection (HTTP 400),
  missing-target rejection, re-ingest stale-edge cleanup.

### Notes

- **Phase 2 ships Python only.** Tree-sitter edge extraction for
  the other 7 supported languages (JavaScript / TypeScript / Go /
  Rust / Java / C++ / C#) lands in a follow-up; the schema, pipeline
  wiring, and resolver are all language-agnostic.
- **Resolver pass not yet shipped.** When `from x import Foo` writes
  an edge with `dst_qualified_name="x.Foo"` and the chunk for
  `x.Foo` doesn't exist yet, `dst_chunk_id` stays NULL. A future
  follow-up pass can populate it once the target file is ingested.
  Read paths handle NULL `dst_chunk_id` correctly.

### All gates green

- `ruff check` ✓
- `ruff format` ✓
- `mypy` (469 source files) ✓
- `check_sloc.py --enforce` ✓ (every new module ≤150 SLOC)
- 792 tests pass (unit + e2e + integration + invariants)

## 1.4.0 — 2026-05-09

Phase 1 of the V1.4→V2 code-memory roadmap (see
`docs/V1_4_TO_V2_ROADMAP.md`): the file-ingest pipeline now produces
**symbol-level chunks across all top-7 supported languages**
(JavaScript, TypeScript, Go, Rust, Java, C++, C# alongside Python),
not just Python. Each declaration node — function, class, method,
struct, enum, interface, type alias — becomes its own chunk with an
indexable qualified name, so a search for `paperBot.calculate` lands
precisely on the method body. Tree-sitter is an OPTIONAL dependency;
when grammars aren't installed the dispatcher falls back to the
existing token-window split with zero functional regression.

### Headline — symbol-level chunks across 7 new languages

- **Tree-sitter dispatcher**
  (`chunking/code.py` + `chunking/code_python.py` + `chunking/code_ts.py`).
  Python keeps its zero-dep stdlib `ast` fast-path; everything else
  routes through `chunking/symbol_query.py` (recursive walker over
  the tree-sitter AST) backed by `chunking/ts_grammar.py` (lazy,
  cached parser loader). Per-language quirks isolated in
  `chunking/symbol_naming.py` (Go's `type_declaration` wraps a
  `type_spec`; C++ buries function names inside `function_declarator`)
  and the declaration tables live in `chunking/symbol_decls.py`.
- **Indexable symbol metadata on chunks** (migration 0028 — three
  new columns: `symbol_kind`, `qualified_name`, `parent_qualified_name`).
  Every code chunk emitted by the structural chunker carries the
  symbol's kind (`function | class | method | struct | interface |
  enum | type`), its qualified name (e.g. `paperBot.calculate` or
  `Calculator::add` for C++), and the parent container's qualified
  name when the symbol is a method. Pre-1.4.0 chunks keep
  `qualified_name = NULL` and remain visible only via FTS / vector
  retrieval. Two partial indexes (`idx_chunks_qualified_name`,
  `idx_chunks_symbol_kind`) make exact and prefix lookups O(log n).
- **`POST /memory/find_symbols` + `memory_find_symbols` MCP tool.**
  Exact-name match, prefix match, kind filter, language filter — all
  scan only the indexable columns, never the FTS table or the JSON
  metadata blob. Enables agents to fetch the body of one
  `Class.method` without paying for embedding similarity.
- **CODE_LANGS expanded.** The ingest pipeline's `CODE_LANGS` set
  drops `ruby` / `kotlin` (no tree-sitter grammar wired) and adds
  `cpp` / `csharp` so `.cpp` / `.cs` files now go through structural
  chunking by default. JavaScript / TypeScript / Go / Rust / Java
  were already in the set but previously fell through to the
  token-window split.

### Optional dependency surface

`pyproject.toml` `[tree-sitter]` extras now declare all 8 grammar
packages (`tree_sitter_python`, `tree_sitter_javascript`,
`tree_sitter_typescript`, `tree_sitter_go`, `tree_sitter_rust`,
`tree_sitter_java`, `tree_sitter_cpp`, `tree_sitter_c_sharp`) plus
the core `tree-sitter>=0.23,<1.0`. None of them are installed by the
default `pip install agent-memory-lite` — the optional install
(`pip install agent-memory-lite[tree-sitter]`) is what activates the
new structural chunks. The service stays installable on hosts
without C extensions because `chunking/ts_grammar.get_parser()`
silently returns `None` on missing packages.

### Tests

- `tests/unit/chunking/test_code_ts_languages.py` (9 tests, one per
  language plus an unsupported-language fallback case and a
  comment-only-input case) locks the per-language structural shapes
  the dispatcher needs to handle.
- `tests/e2e/test_find_symbols_route.py` (6 tests) locks the e2e
  contract: ingest a Python file → exact-name match, prefix match,
  kind filter, unknown-kind rejection (HTTP 400), TypeScript
  dispatch, workspace isolation.
- Updated `tests/unit/chunking/test_code.py` Python expectations
  remain unchanged (Python AST path is preserved verbatim).

Total new tests: **15**. All gates green: `ruff check` ✓,
`ruff format` ✓, `mypy` (457 source files) ✓,
unit + e2e + integration + invariants ✓.

### MCP surface

New tool: **`memory_find_symbols`**. Schema published via
`stdio_tools_episodes.EPISODE_TOOLS`; handler dispatches HTTP first
(falling back to local SQLite via `tools_symbols.memory_find_symbols`).
Pair-call: use `memory_find_symbols(name="Class.method")` when you
know the symbol, fall back to `memory_search(query="Class.method")`
when you don't.

### Notes for the operator

- **No upgrade action required for existing workspaces.** Pre-1.4.0
  code chunks have `qualified_name = NULL` and continue to surface
  via FTS / vector retrieval. To populate the new columns for
  existing files, re-ingest them (`memory_ingest_file` — idempotent
  by content hash, so unchanged files are no-ops).
- **Phase 1 of 7.** This release ships the structural foundation;
  the hard graph (CALLS / IMPORTS / EXPORTS / EXTENDS edges),
  symbol-level versioning + breaking-change detection, the soft graph
  + active-edit registry for multi-agent coordination, narrative file
  digests, and the dashboard surface land in v1.5.0 → v2.0 per the
  roadmap document.

## 1.3.0 — 2026-05-09

Substantial release closing the highest-ROI items from the v1.2.4
post-release audit and laying groundwork for the v1.4 architectural
code-memory roadmap. Eight features delivered across four areas
(measurement, code awareness, multi-agent attribution, operator
tooling) plus one bug fix from the v1.8 era.

### Headline — measurement

- **`X-Memory-Agent-Id` header + per-agent telemetry partition.**
  `audit_log` gains an `agent_id` column (migration 0026); a new
  `AgentIdentityMiddleware` reads the request header into a
  request-scoped `ContextVar` so `insert_audit` can attribute every
  mutation to its agent client without changing call sites. The
  `/memory/telemetry` endpoint now returns a `by_agent` block
  splitting search vs write counts per agent — Claude vs Codex vs
  hub-mcp vs scripts vs `(unknown)`. Closes the original "we can't
  prove v1.2.4 helped Claude specifically" gap.
- **Search hit-rate.** `/memory/telemetry` adds
  `search_calls_with_hits`, `search_calls_zero_hits`, and
  `search_hit_rate` derived from `after_json.hits` / `.sources` on
  search and get_context audit rows. Surfaces agent queries that
  return nothing — discipline signal.
- **`MEMORY_AUDIT_READS=true`** (continued from 1.2.4) is the
  prerequisite. Read-side audit emission powers everything above.

### Headline — code awareness foundations

- **`references[]` field on decisions** (migration 0027). Each
  decision can declaratively list affected file paths or
  `path:symbol` markers. The `/memory/explain_diff` endpoint then
  matches by exact reference rather than guessing through substring
  search.
- **`POST /memory/explain_diff`** — give it a unified diff or a
  list of files; it returns active decisions whose territory the
  diff touches. Two match modes: `declarative` (precise, via
  `references_json`) and `substring` (fallback for legacy decisions
  written without explicit references). Designed to be called from
  pre-commit hooks or by the agent before non-trivial edits.
- **Python method-level chunks.** `chunk_code` now emits a separate
  chunk per method inside a class (`Foo.bar` qualified name) so
  `memory_search("paperBot.calculate")` lands on the method body
  precisely instead of the whole class. Class chunk and method
  chunks textually overlap; both surface for FTS queries on either
  the class or method name.

### Headline — operator tooling

- **`scripts/memory_pre_commit.ps1`** — Windows PowerShell hook
  that calls `/memory/explain_diff` against the staged diff and
  prints a short report of decisions matching changed files.
  Non-blocking by default; set `MEMORY_PRECOMMIT_BLOCK=1` to abort
  the commit when high-importance decisions match.
- **`scripts/memory_status.py`** — single-screen CLI overview.
  Hits health, hygiene_report, quality_gate, telemetry, and
  cold_decisions, prints a one-page summary with action hints.
  Replaces 5 manual curl calls.
- **`scripts/memory_auto_triage_task.ps1`** (continued from 1.2.4)
  — wrapper for installing nightly auto-triage as a Windows
  scheduled task. ASCII-only literals so Windows PowerShell 5.1
  parses regardless of console code page.

### Headline — operator surfaces

- **`GET /memory/cold_decisions`** — active decisions that haven't
  been retrieved in `cutoff_days` (default 30) or never. Pinned
  flag visible per row so the operator does not accidentally
  archive a critical pinned decision. Kindred to
  `/memory/cold_candidates` but specifically for the decisions
  layer.

### Headline — bug fix from the v1.8 era

- **`/memory/compact` now actually triggers reflective compaction.**
  Pre-1.3.0, the route did NOT pass `settings` to
  `summarize_old_episodes`, which silently disabled the v1.8
  lesson-candidate emission even when
  `MEMORY_REFLECTIVE_COMPACT_ENABLED=true`. Diagnosed in copyBot
  2026-05-09: 332 episodes, 0 insight_candidates ever. Fixed; the
  response now exposes `lesson_candidates_emitted` so the operator
  sees whether the v1.8 pass actually ran. Added
  `MEMORY_COMPACT_AGE_DAYS` env var (default 30) so operators on
  young workspaces can lower the threshold to 7-14 days.

### Headline — capability-link discipline rewrite

- **Seed BI rewritten** for stronger imperative framing
  (`bootstrap/project_memory_seed_behavior.py`). Pre-1.3.0, the
  search-discipline rule reached 73% follow-up but the
  capability-link rule stayed at 20% (live measurement on copyBot
  2026-05-09). Rule now reframes write as a two-step atomic
  action: `memory_write_decision` is step 1, `memory_link_capability`
  is mandatory step 2. Search-rule paired textually with the
  capability-link rule so both rules reinforce each other in the
  envelope.

### Provenance backfill on copyBot (one-time, not a code change)

Operator ran a 60-min-window backfill over important
decisions without source_episode_id: 50 → 29 (-21 backfilled).
Remaining 29 are unrecoverable — written without any episode
within an hour of the decision write.

### v1.4 roadmap landed

- **`docs/V1_4_TO_V2_ROADMAP.md`** — multi-release plan for
  architectural code memory in multi-agent team scenarios.
  Covers symbol-level indexation across 7 languages, hard
  dependency graph, soft (vector) graph, edit registry, conflict
  detection, and narrative synthesis. Each phase is independently
  shippable with a measurable ship-stop gate.

### Added (file-level)

- `src/agent_memory_lite/api/agent_context.py` — request-scoped
  ContextVar for agent identity.
- `src/agent_memory_lite/api/agent_identity_middleware.py` —
  Starlette middleware reading `X-Memory-Agent-Id`.
- `src/agent_memory_lite/api/routes/cold_decisions.py` — the
  cold-decisions endpoint.
- `src/agent_memory_lite/api/routes/explain_diff.py` — the
  diff-to-decision matching endpoint.
- `src/agent_memory_lite/api/routes/telemetry_aggregate.py` —
  pure aggregation helpers split out of telemetry.py for SLOC
  budget and unit-testability.
- `src/agent_memory_lite/repositories/decisions_references.py`
  — references_json (de)serialisation + back-compat INSERT helper.
- `scripts/memory_pre_commit.ps1` — pre-commit hook.
- `scripts/memory_status.py` — operator CLI.
- `scripts/memory_auto_triage_task.ps1` — scheduled task wrapper.
- `migrations/0026_audit_agent_id.sql`,
  `migrations/0027_decision_references.sql` — schema additions.
- 16 new tests across `test_explain_diff_route`,
  `test_cold_decisions_route`, `test_telemetry_per_agent`,
  `test_compact_route`, `test_code` (method-level chunk
  assertions).

### Changed (file-level)

- `src/agent_memory_lite/api/app.py` — wire new middleware + four
  new routes.
- `src/agent_memory_lite/api/routes/context.py` and
  `routes/search.py` — write read-side audit row when
  `MEMORY_AUDIT_READS=true`.
- `src/agent_memory_lite/api/routes/compact.py` — pass `settings`
  through to `summarize_old_episodes` (the v1.8 bug fix).
- `src/agent_memory_lite/api/routes/telemetry.py` — partition by
  agent + hit-rate fields.
- `src/agent_memory_lite/bootstrap/project_memory_seed.py`,
  `seed_templates.py`, `seed_behavior.py` — capability-link rule
  rewrite + DISCIPLINE_FACTORIES registry.
- `src/agent_memory_lite/chunking/code.py` — Python method-level
  chunks via `ClassDef.body` walk.
- `src/agent_memory_lite/config/settings.py` —
  `audit_read_operations` and `compact_age_days` env flags.
- `src/agent_memory_lite/models/decisions.py`,
  `api/schemas/decisions.py`,
  `api/routes/decisions.py`,
  `ingestion/decision_writer.py`,
  `repositories/decisions_repo.py` — references[] field plumbed
  through decision write + read path.
- `src/agent_memory_lite/repositories/audit_repo.py` — agent_id
  argument with ContextVar fallback.
- `docs/AGENT_CONTRACT.md` and all 5 contract surfaces (CLAUDE.md,
  AGENTS.md ×2 plus copyBot mirrors plus `~/.claude/CLAUDE.md`)
  — re-synced canonical with rule 2 "Search liberally"
  consolidation and renumbering.

### Verification

- 781 pytest tests pass (was 765 pre-1.3.0; +16 new).
- mypy clean — no errors across 449 source files.
- ruff check + ruff format clean.
- SLOC ceiling enforced — every source file at or below 150 SLOC.
- Pre-push crash test: 27 phases / 133 assertions PASS.
- All 5 contract surfaces byte-identical to canonical
  `docs/AGENT_CONTRACT.md`.

### Notes for next release

The roadmap commits to v1.4.0 starting next: tree-sitter dependency
plus symbol-level chunking for the top-7 languages (Python, JS, TS,
Go, Rust, Java, C++ / C#). Foundation only — no graph yet. Ship
gate: hit-rate stays at or above the 1.3.0 baseline; no regressions.

## 1.2.4 — 2026-05-05

Closes the Codex-vs-Claude search-rate gap observed on copyBot
(Codex called `memory_search` 8-12× per session, Claude 0-2×) plus
two leftover noise sources from v1.10/v1.2.3. Adds the first
operator-facing measurement endpoint so behaviour change is verifiable
on the next workspace day instead of hand-counted.

### Added — measurement (new operator surface)

- `GET /memory/telemetry?workspace_id=...&days=N` — partition
  audit_log into search vs write buckets and return
  `search_total`, `write_total`, `search_per_write_ratio`, `per_day`
  list, `by_action_top` (top 15 actions). Bookkeeping events
  (`sentinel.run_recorded`, `ui_event`) are excluded so the ratio
  reflects real agent behaviour. Operator interpretation:
  ratio < 0.5 → discipline gap; 0.5–1.5 → balanced; > 1.5 → search-heavy.
- `MEMORY_AUDIT_READS=true` (new env flag, default ON) —
  `memory_search` and `memory_get_context` routes now write a
  lightweight `audit_log` row after responding so the telemetry
  endpoint can count read calls. Pre-1.2.4 only writes were
  audited; the partition was empty for the search bucket. Per-call
  cost is microseconds; set to `false` if your workspace's
  audit_log volume budget is tight.

### Added — behavioral nudges for the agent

- `bootstrap/project_memory_seed_behavior.py::search_before_write_discipline_instruction`
  factory + `DISCIPLINE_FACTORIES` registry. The neutral seed now
  writes a second project-AGNOSTIC discipline behavior_instruction
  alongside the 1.2.3 capability-link rule:
  - **"Search before write — auto-inject is not exhaustive"**
    Before any non-trivial write the agent must call
    `memory_search` (file path / error string / domain term) AND
    `memory_list_decisions(include_superseded=true)` for
    architectural pivots. Adds explicit guidance on FTS-mode for
    exception strings and symbol names.
  - Adding future generic discipline rules is now one line —
    append to `DISCIPLINE_FACTORIES`; orchestrator iterates the
    registry without hardcoding factories.
- `docs/AGENT_CONTRACT.md` Operating-contract list — old rules 2
  ("before editing a file") and 3 ("before changing architecture")
  consolidated into the new emphatic **rule 2 "Search liberally —
  auto-inject is not exhaustive"** with three sub-bullets covering
  file edits, architectural decisions (with explicit
  `include_superseded=true` reminder), and exception strings (FTS
  mode). Rule 1 expanded with explicit "RRF-truncated to a token
  budget — what did NOT fit is invisible from this call alone".
  Rules 4-29 renumbered to 3-28 for clean ordering. Block
  re-injected into all five contract surfaces.
- `<index>` blocks across every envelope section now include a
  `<hint>` line: "Long tail past the rendered top-N is NOT in
  this envelope. Call memory_get_object(kind, id) on any
  &lt;ref/&gt; that matters, OR memory_search with a sharper
  query, OR re-call memory_get_context with historical=true." The
  `<index>` was previously easy to read as cosmetic; the hint
  makes it actionable.

### Fixed — leftover noise from prior releases

- **LLM-extractor produced spurious CORRECTION-kind candidates**
  from `agent_action` episodes. Live regression in copyBot
  2026-05-05: a Phase 7.A.1 implementation report ("HIGH issue
  X — fixed") generated 3 fake CORRECTION candidates that polluted
  the operator review queue. Fixed in
  `extraction/llm_extractor.py::_parse` — CORRECTION-kind items
  are now dropped when the source episode's `source_type` is not
  `USER_MESSAGE`, restoring the v1.10 (claim, correction) pair
  semantics. Regression test:
  `tests/unit/extraction/test_llm_extractor_correction_filter.py`.
- **`behavior_instruction_without_source` quality_gate warning
  fired on legitimate seed-bootstrap BIs** added in 1.2.3. Fixed
  in `maintenance/quality_gate_behavior.py` — `seed_bootstrap`
  added to the authoritative-source allowlist alongside `manual`
  and `system_seed` for both the without-source warning AND the
  prompt-injection-risk error. Regression test:
  `tests/unit/maintenance/test_quality_gate_seed_bootstrap.py`.

### Added — operator tooling

- `scripts/memory_auto_triage_task.ps1` — Windows scheduled-task
  wrapper for nightly auto-triage. Actions: Install / Uninstall /
  Status / RunNow. Default time 03:30, opt-in `-Apply` flag (must
  be explicit to actually mutate; without it the task runs as
  dry-run only). Log lands in
  `<project>/.agent_memory/logs/auto_triage.log`. Pairs with
  `memory_service_task.ps1` for the HTTP service install. Closes
  the "manual auto-triage runs leave debt accumulating between
  prompts" gap.

### UI

- LIVE TRAIL panel now clusters consecutive same-intent events
  whose `started_at` falls within 2 seconds of each other into a
  single visual row with `intent ×N` badge + "+N more" suffix in
  the prompt cell. Click to expand → individual children with
  their per-call timestamps and durations. Underlying
  `state.trailGroups` is untouched, so replay/observatory pipeline
  is unaffected. Fixes "three SEARCH rows at the same timestamp
  flooding the trail" feedback from copyBot operator.

### Live verification

Ran phases A-D of the audit playbook against production copyBot on
2026-05-05:

- Backed up `memory.db` as `memory.db.pre-fix-2026-05-05`.
- Re-ran `setup_agent.py --project copyBot` (without
  `--no-seed-memory-bootstrap`) → both seed BIs now live in
  copyBot memory.db (`beh_758aa5cdd7987304` capability-link,
  `beh_c5ee6cbc13c0152d` search-first).
- Rejected 9 noise CORRECTION candidates (6 system-block false
  positives, 3 LLM-extractor false positives now blocked at the
  source).
- Backfilled 60 of 105 important decisions with provenance via
  10-min and 30-min time-window matching to nearest episode.
- Auto-triage applied 57 capability_links via semantic matching.

Resulting movement on copyBot quality:
- hygiene_report: 105 → 43 findings (−59%)
- quality_gate: 93 → 32 findings (−66%); error count 36 → 31
- CORRECTION review queue: 9 pending → 0 pending
- behavior_instructions: 45 → 47 active (the 2 seed BIs)
- capability_links: 145 → 202

### Verification

- 762 pytest tests pass (4 new telemetry, 1 new index-hint, 4 LLM
  filter, 3 quality_gate seed_bootstrap; remaining match prior
  count).
- ruff check + ruff format --check clean.
- SLOC ceiling enforced — all source files at or below 150.
- Pre-push crash test: 27 phases / 133 assertions PASS.
- All 5 contract surfaces (agent-mem CLAUDE.md/AGENTS.md,
  copyBot CLAUDE.md/AGENTS.md, ~/.claude/CLAUDE.md) byte-identical
  to the canonical `docs/AGENT_CONTRACT.md`.

### Notes

- Telemetry won't show search calls until the HTTP service
  restarts to pick up `MEMORY_AUDIT_READS=true` and the new
  audit-row code path. Pre-restart calls remain unaudited and
  invisible to telemetry.
- The new `<hint>` adds ~200 chars per index block (one line per
  section that has a long tail). Net envelope growth is small —
  hints only appear when there ARE hidden items, sections that
  render every item have no `<index>` and no hint.
- Existing copyBot now has the 2nd seed BI live — agents in next
  Claude Code session will see "Search before write" rule in
  every `<behavior_instructions>` envelope.

## 1.2.3 — 2026-05-05

Closes the structural cause of the "decisions and theories without
capability link" debt observed across long-running workspaces (copyBot
hit 53 missing-link findings on ~150 research objects in a week despite
having 12 roles + 35 skills + 15 playbooks defined). The root cause was
discipline drift: agents wrote decisions/theories but skipped the
follow-up `memory_link_capability` call. The fix is structural — the
neutral project-memory seed now writes one project-AGNOSTIC discipline
`behavior_instruction` that lands in every workspace's
`<behavior_instructions>` envelope, so the next agent reads the rule
before the first write of the session.

### Added

- `src/agent_memory_lite/bootstrap/project_memory_seed_behavior.py` —
  new module hosting `link_capability_discipline_instruction()`. Split
  out of `project_memory_seed_templates.py` to keep both files under
  the 150-SLOC ceiling and to make the "where to add new generic
  discipline rules" location explicit. Each rule must be project-
  AGNOSTIC (no language, personality, or project-specific behavior);
  project-specific behavior_instructions remain operator-driven via
  `memory_upsert_behavior_instruction`.
- `link_capability_discipline_instruction(workspace_id, source_episode_id)`
  factory returns a `BehaviorInstructionIn` with:
  - name: "Link capability after every decision and theory write"
  - kind: `operating_rule`
  - scope: `workspace`
  - priority: `user_preference`
  - conflict_policy: `current_user_wins` (operator can always override)
  - applies_to: `[memory_write_decision, memory_write_theory,
    memory_add_theory_evidence, memory_write_experiment]`
  - source_type: `seed_bootstrap`
- 2 new tests in `tests/test_project_memory_seed.py` plus updates to
  the 2 existing tests so the seed write of one BI is locked in.

### Changed

- `src/agent_memory_lite/bootstrap/project_memory_seed.py` —
  `seed_neutral_project_memory()` now calls
  `upsert_behavior_instruction()` after seeding skills/playbook/concepts.
  `ProjectMemorySeedResult.behavior_instructions` is a new list field;
  `behavior_instructions_written` is now a derived property
  (kept for backward-compat with operators reading the JSON output).
- `docs/AGENT_CONTRACT.md` — doctrine clarified: seed may write
  generic discipline `behavior_instructions`; project-specific
  language / style / personality rules remain operator-driven. Block
  re-injected into all five contract surfaces (agent-mem CLAUDE.md,
  AGENTS.md; copyBot CLAUDE.md, AGENTS.md; `~/.claude/CLAUDE.md`).
- `bootstrap/project_memory_seed_templates.py::memory_bootstrap_playbook` —
  one of its `success_criteria` lines was "No behavior instruction
  was seeded"; refined to reflect the new doctrine.

### Notes

- **Seed is idempotent.** Re-running `setup_agent.py --project <path>`
  on an existing workspace does NOT duplicate the BI; upsert keys on
  `(workspace_id, name)`. Existing manually-created behavior_instructions
  in the workspace are untouched.
- **Existing workspaces don't auto-upgrade.** The new BI lands only
  when the seed runs (next `setup_agent.py --project ...` without
  `--no-seed-memory-bootstrap`, OR a fresh project init). Live in
  copyBot 2026-05-05: ran the seed against the production memory.db
  → BI inserted at `beh_758aa5cdd7987304`,
  hygiene findings 105 → 43 (-59%), quality_gate findings 93 → 32
  (-66%), all 9 noise CORRECTION candidates rejected, 60 important
  decisions backfilled with provenance, 57 capability_links applied
  via auto-triage.
- **Operator override remains supreme.** A workspace that wants
  stricter or laxer discipline rules can `memory_archive` this BI
  or upsert a replacement; `current_user_wins` policy guarantees
  any explicit operator instruction in the same session takes
  precedence.

## 1.2.2 — 2026-05-05

Patch release — closes a heuristic false-positive in the v1.10
correction loop that surfaced after a day of real-world use.

### Fixed

- **Correction heuristic produced noise on Claude Code system blocks**
  (`extraction/correction_patterns.py`). Live regression observed
  in copyBot 2026-05-05: a single afternoon produced 6 candidates
  with subject `Verify before claiming: <task-notification>` —
  Claude Code wraps runtime tool/notification output in
  `<task-notification>...`, `<command-name>...`, `<ide-selection>...`,
  `<system-reminder>...`, `<command-message>...` tags before they
  reach the prompt. The heuristic was matching on text inside these
  wrappers as if it were a user correction. Added
  `SYSTEM_BLOCK_OPENER` regex; `match_correction()` short-circuits
  to `matched=False` when the message starts with `<tag>`. Inline
  tag mentions (e.g. "use the `<task-notification>` markup") are
  unaffected because the regex requires the tag at message start.

### Added

- `tests/unit/extraction/test_correction_patterns.py` — 6 new tests:
  5 system-block false-positive cases (`task-notification`,
  `command-name`, `ide-selection`, `system-reminder`, system-block
  with correction body inside) all asserting no match, plus 1
  positive test that an inline tag mention in a real correction
  still matches.

### Notes

- This is a heuristic-only change. The same noise also appeared in
  copyBot's queue from a SEPARATE path: the LLM (Ollama) extractor
  produced 3 CORRECTION-kind candidates from an `agent_action`
  episode (Phase 7.A.1 implementation report) by reading audit-
  findings phrasing as "fixed issues". That separate path is NOT
  covered by this patch — see roadmap for future v1.x work to
  constrain LLM-extractor's CORRECTION semantics.
- Operator playbook for cleaning the existing copyBot queue: reject
  all 9 pending CORRECTION candidates (none are real corrections of
  agent claims), restart HTTP service + MCP stdio to pick up 1.2.2.

## 1.2.1 — 2026-05-04

Hardening patch on top of 1.2.0. A four-AI-agent post-release audit
found one critical and three high-severity issues in the just-shipped
correction-promotion surface. All four are fixed; each carries a paired
regression test so the gap can't come back.

### Fixed

- **CRITICAL — Atomic promotion** (`ingestion/correction_promotion.py`).
  Pre-1.2.1, `promote_correction_to_behavior` made three independent
  commits: behavior_instruction upsert, optional pin, candidate flip +
  audit. A failure mid-flow could leave a behavior_instruction live
  but the candidate stuck at `status='new'`, producing a confusing
  `409 name_taken` on retry. The three steps now run inside one
  outer `with_tx`. Inner helpers' own `with_tx` calls become
  SAVEPOINTs (see the `db/transactions.py` change below). On any
  failure the entire promotion rolls back and the operator can retry
  cleanly.
- **HIGH — `coerce_applies_to` accepted tuple silently dropping it
  to empty** (`ingestion/correction_promotion_guards.py`). The old
  `isinstance(value, list)` check rejected an already-coerced tuple
  and returned `()`. Now accepts both list and tuple, returns `()`
  for `None`, and raises `TypeError` on unknown types (dict, int,
  …) so bad input surfaces as a 422 at the boundary rather than a
  silent empty list.
- **HIGH — `record_throttle_rejection` called `conn.commit()` on the
  shared connection** (`extraction/correction_distill.py`). In the
  current call graph the commit was a no-op, but if any future
  refactor invokes the throttle helper from inside a `with_tx`
  block, the explicit commit would have prematurely finalized the
  outer transaction. Removed; in autocommit mode each `insert_audit`
  statement is its own implicit transaction so the throttle row
  still persists.
- **MEDIUM — Hook `transcript_path` had no allowlist**
  (`scripts/transcript_pair_extractor.py`). A compromised or
  misconfigured hook caller could pass an arbitrary readable path
  (`/etc/passwd`, another user's transcript) and have its tail
  bytes ingested as the agent claim. Now restricted to
  `~/.claude/` plus directories listed in
  `AGENT_MEMORY_TRANSCRIPT_ROOTS` (os.pathsep-separated). Path
  resolution rejects traversal attacks via `Path.resolve()` +
  `relative_to()`.

### Changed

- `db/transactions.py` — `with_tx` is now nest-safe. When entered
  while an outer transaction is already open, it issues a SAVEPOINT
  instead of a nested `BEGIN` (which SQLite forbids). Top-level
  callers see no behaviour change. This is what makes the atomic
  promote possible without rewriting `upsert_behavior_instruction`
  and `pin_memory_object`. RELEASE is now in a `finally` with
  `contextlib.suppress(sqlite3.Error)` so the savepoint is always
  cleaned up even if the rollback path itself errors. Token width
  upgraded from 4 to 8 bytes for collision safety in deeply nested
  batch jobs.
- `ingestion/pin_service.py` — `_set_table_pinned` now wraps the
  UPDATE in `with_tx` instead of an explicit `conn.commit()`. Top
  level still BEGIN/COMMITs; nested inside the atomic promote
  becomes a SAVEPOINT.
- `repositories/decisions_repo.py` — `set_decision_pinned` migrated
  to `with_tx` for symmetry with `pin_service._set_table_pinned`.
  Pre-1.2.1 the helper called `conn.commit()` directly; while no
  current code path pins a decision from inside an outer
  transaction, the asymmetry was a future foot-gun. Top-level
  callers see no behaviour change.

### Added

- `tests/unit/ingestion/test_correction_promotion.py` — 11 regression
  tests covering: atomic rollback on Step-1 / Step-2 / Step-3
  failure, happy-path end-to-end, `coerce_applies_to`
  tuple / dict / None / mixed list, throttle helper not committing
  outer tx, `with_tx` savepoint nesting, savepoint rollback on inner
  failure, `wrong_kind` guard still fires pre-write.
- `tests/unit/scripts/test_transcript_pair_extractor.py` — 3 new
  tests for the M3 allowlist (rejects path outside `~/.claude/`,
  rejects relative paths in env override, respects absolute env
  override) plus an autouse fixture so the existing 9 tests
  continue to pass against `tmp_path`.

### New env var

- `AGENT_MEMORY_TRANSCRIPT_ROOTS` (optional, default empty) —
  os.pathsep-separated **absolute** paths. Add to extend the
  transcript-read allowlist beyond the built-in `~/.claude/`.
  Relative entries are silently dropped (cwd is non-deterministic
  across hook fork points).

### Notes

- M3 is technically a behaviour change for hook callers that
  previously passed transcript paths outside `~/.claude/` (e.g.
  integration test harnesses pointing at `/tmp/`). The
  `AGENT_MEMORY_TRANSCRIPT_ROOTS` override re-allows specific
  external roots so existing test setups can adapt without code
  changes.

## 1.2.0 — 2026-05-04

**Headline:** v1.10 correction-aware learning loop. When the operator
corrects the agent's claim in chat, the system now captures the pair
automatically, proposes a one-line behavior fix, and queues it for
operator review. Promoted candidates land in
`<behavior_instructions>` and surface in every future envelope — so
the next session reads the rule before answering and tunes its caution.

This closes the **structurally missing loop** observed in 1.1.1: the
agent saw user corrections, fixed the immediate thing, then forgot
the lesson. v1.10 makes the loop automatic for the highest-frequency
operator action — correcting the agent — turning *"memory shapes
behavior via operator-trace"* from a README claim into an actual
mechanism.

See [`docs/V1_2_0.md`](docs/V1_2_0.md) for the operator runbook,
heuristic patterns, env-flag map, episode-dedup bypass rationale, and
the full validation matrix.

### Architecture (three stages, all behind one master flag)

1. **Capture** (`scripts/inject_memory_context.py` UserPromptSubmit hook)
   – Reads the Claude Code transcript JSONL referenced by `transcript_path`,
     locates the most recent assistant text turn within
     `MEMORY_CORRECTION_PAIR_WINDOW_MIN` minutes (default 30).
   – If the current user prompt matches the correction heuristic
     (regex over Russian + English contradiction patterns), ingests
     two episodes back-to-back: the agent claim
     (`metadata.kind=correction_target`) and the user correction
     (`metadata.kind=user_correction` with
     `correction_target_episode_id` cross-reference).
2. **Extract** (`src/agent_memory_lite/extraction/correction_extractor.py`)
   – New `Extractor` registered alongside `HeuristicExtractor` and
     the Ollama LLM extractor; runs on every ingest.
   – On a `user_correction` episode, looks up the paired claim,
     distills a one-line behavior rule via template (`Verify before
     claiming: …`), emits a `MemoryCandidate(kind=CORRECTION)` with
     0.5 / 0.7 / 0.85 confidence based on regex specificity.
3. **Promote** (`POST /memory/promote_candidate_to_behavior`)
   – Operator-driven, never auto-fires. Calls
     `upsert_behavior_instruction` with `source_type="memory_candidate"`,
     `source_id=<candidate.id>` so lineage is preserved. Updates
     candidate to `status='promoted'` with `promoted_target_*` filled.

### Added

- `src/agent_memory_lite/extraction/correction_patterns.py` — regex
  pattern set with bilingual openers (`нет, ` / `no, ` / `wait,` /
  `actually,` / `неправильно` / `я буквально` / `i literally`) and
  body markers (`не мог`, `это не так`, `that doesn't`,
  `you're wrong`, `cant/can't`). Plus a negative filter for
  agreement-with-negation phrases (`нет проблем`, `no problem`).
- `src/agent_memory_lite/extraction/correction_extractor.py` —
  `CorrectionExtractor(conn)` Extractor protocol implementation.
  Includes a per-workspace per-day throttle, workspace_id-scoped
  claim resolution (security), and audit-traced throttle rejections.
- `src/agent_memory_lite/extraction/correction_distill.py` —
  pure-function helpers (`distill_rule`, `clip`,
  `count_corrections_today`, `record_throttle_rejection`,
  `build_correction_candidate`) split out so the extractor stays
  under the 150-SLOC ceiling.
- `src/agent_memory_lite/ingestion/correction_promotion.py` —
  shared service used by both the HTTP route and the MCP stdio
  handler so promotion semantics are identical across surfaces.
- `src/agent_memory_lite/ingestion/correction_promotion_guards.py` —
  guard helpers (`guard_name_taken`, `coerce_applies_to`,
  `CorrectionPromotionError`) split out so the service module stays
  under the 150-SLOC ceiling. The name-collision guard is what makes
  `overwrite=False` (the default) refuse to silently clobber an
  existing same-name behavior_instruction.
- `src/agent_memory_lite/api/routes/promote_to_behavior.py` +
  `api/schemas/promote_to_behavior.py` — new endpoint
  `POST /memory/promote_candidate_to_behavior`.
- `scripts/transcript_pair_extractor.py` — read-only Claude Code
  JSONL parser; tail-bounded (~400 lines) and best-effort (returns
  `None` on any parse error).
- `scripts/crash_test/phases/p26_v110_correction.py` — full
  end-to-end crash-test phase (claim → correction → candidate →
  promote → envelope check).
- `tests/unit/extraction/test_correction_patterns.py` (25 tests
  including hypothesis property tests).
- `tests/unit/extraction/test_correction_extractor.py` (8 tests).
- `tests/unit/scripts/test_transcript_pair_extractor.py` (11 tests).
- `tests/e2e/test_promote_to_behavior.py` (9 route round-trip tests
  covering happy path, `pinned=true`, `overwrite=true` replacement,
  name-collision 409, wrong-kind 409, and the
  `rule_text_override` length cap).
- `tests/integration/test_correction_loop_e2e.py` (full pipeline +
  flag-off check).
- `tests/integration/test_correction_detector_on_corpus.py` —
  retrospective verification: detector catches the three documented
  corrections from the v1.10 design session.
- `tests/invariants/test_v110_parity.py` — locks flag-off behavior
  byte-equivalent to v1.1.1.
- `tests/unit/mcp/test_correction_via_mcp_local.py::test_mcp_promote_to_behavior_schema_exposes_full_field_set`
  — regression test that asserts the full 13-field set
  (`workspace_id`, `candidate_id`, `name`, `rule_text_override`,
  `rationale`, `kind`, `scope`, `priority`, `conflict_policy`,
  `applies_to`, `decided_by`, `pinned`, `overwrite`) is present in
  the stdio `inputSchema` for `memory_promote_candidate_to_behavior`.
  Caught by a post-release four-AI-agent audit pass.

### Changed

- `src/agent_memory_lite/extraction/thresholds.py` — `CORRECTION`
  threshold lowered from `(0.85, 0.70)` to `(0.5, 0.5)` so heuristic
  matches in the 0.5–0.85 range surface for review. Trust gate
  remains enforced at the promote step.
- `src/agent_memory_lite/ingestion/auto_promote.py` —
  `_build_extractors` now accepts an optional connection so the
  `CorrectionExtractor` can resolve paired claim text.
- `src/agent_memory_lite/retrieval/pending_review.py` — surfaces
  `correction_candidate` queue alongside `decision_candidate` and
  `insight_candidate`, with a hint pointing at the new promote
  endpoint.
- `scripts/inject_memory_context.py` — adds
  `_maybe_capture_correction()` helper; runs best-effort before the
  normal context-injection path.

### Env flags (every default ON; flag-off path locked by parity test)

```
MEMORY_CORRECTION_DETECT_ENABLED=true
MEMORY_CORRECTION_TRANSCRIPT_READ_ENABLED=true
MEMORY_CORRECTION_MIN_USER_LEN=30
MEMORY_CORRECTION_MIN_AGENT_LEN=50
MEMORY_CORRECTION_MIN_CONFIDENCE=0.5
MEMORY_CORRECTION_MAX_PER_DAY=20
MEMORY_CORRECTION_PAIR_WINDOW_MIN=30
```

### Audit-log additions

- `extraction.correction_detected` (when the CorrectionExtractor
  emits a candidate; written by the existing
  `write_memory_candidate` audit path).
- `memory_candidate.promoted_to_behavior` (operator promoted via
  the new endpoint).

### Breaking changes

None. All v1.10 behaviour is gated behind
`MEMORY_CORRECTION_DETECT_ENABLED`. Set to `false` in `.env` to
restore byte-equivalent v1.1.1 behavior; the parity invariant
test enforces this in CI.

### Hardening (post-design audits)

Six rounds of adversarial AI-agent audits found and fixed:
- **SECURITY**: workspace_id check + provenance check
  (`metadata.correction_role="claim"` required) in
  `_resolve_claim` so a forged `correction_target_episode_id` cannot
  leak text from a foreign or unrelated same-workspace episode.
- **DATA-LOSS**: episode dedup now bypasses correction pairs in
  `ingest_episode` — without this, a repeated correction would be
  silently collapsed into the previous episode and the recurring
  mistake would never surface as a second candidate. Locked by
  `tests/integration/test_correction_loop_e2e.py::
  test_correction_pair_bypasses_episode_dedup`.
- **AUDIT**: throttle rejection (`extraction.correction_rejected_throttled`)
  and `overwrite=true` now both land in `audit_log` so operator
  history is complete.
- **ATOMICITY**: promotion writes the durable `behavior_instruction`
  first, then optional pin, then candidate-flip + audit; on partial
  failure the operator-recoverable state is preserved.
- **NAMESPACE**: episode metadata switched to `correction_role` to
  avoid future collisions with other `metadata.kind` users; legacy
  `metadata.kind` still accepted for backward compat.
- **MCP PARITY**: shared `coerce_applies_to` helper used by both HTTP
  and MCP paths so a stringy `applies_to` doesn't split into a
  per-character tuple. New endpoint `memory_promote_candidate_to_behavior`
  registered in MCP stdio + dispatch + tool-registry.
- **NAME COLLISION**: promote refuses to silently replace an active
  rule with the same name unless `overwrite=true` is explicit.
- **SCHEMA**: `rule_text_override` and `rationale` capped at 2000 chars
  so a 100KB injection can't bloat the envelope.
- **PATTERN COVERAGE**: added em-dash + en-dash to Russian opener,
  added first-person fact-evidence opener (`я буквально`,
  `я только что`, `i literally`), tilde expansion for transcript
  paths.
- **LIVE VERIFICATION**: full HTTP loop validated against the running
  service (claim → correction → candidate → promote → behavior_instruction
  → envelope) on both `agentLight` and `copyBot` workspaces.
- **POST-RELEASE AUDIT**: a separate four-AI-agent audit pass on the
  shipped surface caught two real gaps. (1) `stdio_tools_review.py`
  declared `rationale` and `applies_to` in the
  `memory_promote_candidate_to_behavior` `inputSchema` but was missing
  the `overwrite` boolean — the Python-side handler at
  `tools_review.memory_promote_candidate_to_behavior` already read
  it, so MCP stdio clients silently lost the
  name-collision-replace path. Fixed in this same release surface
  with regression test added (see `Added` section).
  (2) `docs/AGENT_CONTRACT.md` JSON example listed only 10 of the 13
  request-body fields (`rationale`, `applies_to`, and `overwrite`
  were undocumented). Updated and re-synced via
  `setup_agent.py --sync-repo` and `--project copyBot` so the
  canonical block is byte-identical across all five contract
  surfaces (`agent-memory-lite/CLAUDE.md`, `AGENTS.md`,
  `copyBot/CLAUDE.md`, `AGENTS.md`, and the operator's global
  `~/.claude/CLAUDE.md`).

### Documentation cross-references

- `README.md` — added a "Latest release: v1.2.0" callout near the
  top pointing at `CHANGELOG.md` and `docs/V1_2_0.md`.
- `CLAUDE.md` (project section, outside the contract block) — added
  a pointer to `docs/V1_2_0.md` from the v1.10 subsection so the
  operator runbook is one click away from the "Memory-quality
  features" map, paralleling the existing `V1_1_0.md` pointer.

## 1.1.1 — 2026-05-04

**Headline:** UI observatory bug fixes + demo carousel hardening. Pure
patch — no behaviour change in retrieval, ingestion, scoring, or
storage. Every flag, every endpoint, every wire format identical
to 1.1.0.

### Fixed

- **UI: orb lit but spokes never drew** during burst writes
  (`src/agent_memory_lite/ui/app.js:1836-1864`). Every `graph_delta`
  event was firing `state.liveLight.set(fid, ...)` immediately,
  lighting the family bubble for 5s. Meanwhile the matching cycle
  was queued behind earlier ones; while the active cycle drew spokes
  for *its* families, the next-queued families' bubbles were
  pre-lit by liveLight without spokes — the user saw a "ghost" orb.
  Fix: skip liveLight when the event has a `request_id` (those
  always produce a cycle that lights the bubble at the correct
  moment via `drawFamilies`). Bare events without a request_id keep
  the pulse as their only feedback signal.
- **UI: "Skills" rendered as a separate node inside Skills family**
  (`src/agent_memory_lite/ui/app.js:1031-1055`). When a `get_context`
  cycle hit multiple capability sub-tables (e.g. `agent_skills` +
  `capability_links`), the `agent_skills` sub-family bubble was
  labeled "Skills" — duplicating the parent family label. Same
  latent issue for `episodes` inside Episodes, `decisions` inside
  Decisions, etc. Fix: `drawSubFamily` now suppresses the sub-family
  label text when it equals the parent family label. The bubble
  itself remains so the structural grouping stays visible; only
  the redundant text is dropped.

### Added

- `scripts/demo_carousel.sh` — 15-step memory churn carousel for
  README video / GIF recording. Hits every action category: search,
  ingest, write_decision, pin, upsert (concept / skill),
  link_capability, archive (decision / episode), accept insight
  candidate, reject decision candidate, update_task_state, explain.
  Runs ~50s, deterministic — steps 12/13 inject fresh pending
  candidates so the demo always exercises the accept/reject paths.
- `docs/demo.gif` — live observatory demo embedded at the top of
  README.md.
- `docs/OPERATIONS.md` — operator runbook covering upgrade workflow,
  service auto-start (Task Scheduler vs Startup folder vs manual),
  hook fallback chain, hub-mode + legacy-DB behaviour, common
  failure modes.

## 1.1.0 — 2026-05-04

**Headline:** six feedback loops (v1.4 through v1.9) plus three follow-on
improvements (v2.1 / v2.2 / v2.3) ship default ON. Every flag flips off
via a single explicit `false` in `.env`; `tests/invariants/test_v2_parity.py`
locks the flag-off path as byte-equivalent to v1.0.x.

### Difference vs 1.0.0

| Surface | 1.0.0 | 1.1.0 |
|---------|-------|-------|
| **Test count** | ~360 | **633** (+273) |
| **Migrations** | 0001 (consolidated) | 0001 + 0020-0025 |
| **Default flags** | every quality flag OFF | every quality flag **ON** (calibrated) |
| **Scoring formula** | semantic+keyword only (other terms hardcoded 0) | full formula with importance/recency/confidence/feedback_ewma/graph |
| **Operator feedback** | manual `record_usage_feedback` only | derived automatically from archive/promote/link |
| **Capability counters** | static `confidence` only | usage / success / failure / last_invoked tracked |
| **Behavior application** | static rule list | `application_count` advances on each render |
| **Cold detection** | manual audit only | automatic `last_retrieved_at` + `cold_candidate` events |
| **Theory promotion** | manual decision_write | validated theories surface as `decision_candidates` (review-only) |
| **Compaction** | text-digest only | optional Ollama lesson extraction → `insight_candidates` |
| **Hygiene findings** | ephemeral (per-request) | persisted with recurrence counts |
| **Sentinel runs** | external cron only | trigger-on-traffic background scheduler |
| **Pending queue surface** | separate `/memory/list_candidates` call | inline `<pending_review>` envelope block |
| **MCP-vs-HTTP parity** | MCP local fallback skipped post-build hooks | single chokepoint (`apply_post_build_hooks`) called by both |

### What 1.1.0 adds (env-flag map; every default ON)

**v1.4 feedback-aware scoring** — completes the retrieval scoring formula
with a feedback-EWMA term over decisions / theories / chunks. New module
`retrieval/feedback_aggregator.py`. `MEMORY_FEEDBACK_EWMA_ENABLED=true`,
halflife 14d, self-loop guard, per-day-per-source cap of 10.
**Migration 0020** adds `feedback_ewma` column + `memory_usage_feedback.source`.

**v1.5 capability maturity + behavior tracking** — usage / success
counters on `agent_skills` / `agent_roles` / `agent_playbooks`;
`behavior_instructions.application_count` advances on each render. New
modules `capability/usage_tracker.py` + `capability/maturity.py` +
`capability/behavior_apply.py`. `MEMORY_CAPABILITY_MATURITY_ENABLED=true`,
`MEMORY_BEHAVIOR_APPLY_TRACKING_ENABLED=true`. **Migration 0021**.

**v1.6 cold-memory lifecycle** — `last_retrieved_at` stamping on top-K
retrieval (batched audit, batch_size=100); cold scanner emits
`cold_candidate` maintenance events for rows untouched > 60 days. Two-flag
split: tracking + auto-queue. `MEMORY_COLD_TRACKING_ENABLED=true`,
`MEMORY_COLD_AUTO_QUEUE_ENABLED=true`. **Migration 0022**.

**v1.7 theory → decision_candidates bridge** — validated theories with
≥ 3 supporting evidence rows surface as pending decision candidates.
Trust gate intact: never auto-promotes. `MEMORY_THEORY_BRIDGE_ENABLED=true`,
`MEMORY_THEORY_BRIDGE_MIN_EVIDENCE=3`. **Migration 0023** adds
`decision_candidates` table.

**v1.8 reflective compaction** — `/memory/compact` runs an Ollama pass
over recent episodes and proposes lessons into `insight_candidates`.
Lesson must cite ≥ 4 source episodes; cap 10 per run. Gracefully degrades
when Ollama unreachable. `MEMORY_REFLECTIVE_COMPACT_ENABLED=true`.
**Migration 0024** adds `insight_candidates` table.

**v1.9 hygiene recurrence + sentinel persistence** — hygiene findings
and watchdog runs persisted with recurrence counts.
`MEMORY_HYGIENE_PERSIST_ENABLED=true`,
`MEMORY_SENTINEL_PERSIST_ENABLED=true`. **Migration 0025** adds
`recurrence_count` / `first_seen_at` / `last_seen_at` on
`maintenance_events` + `retrieval_sentinel_results` table.

**v2.1 implicit feedback** — derives `memory_usage_feedback` rows from
existing operator actions (archive=-1.0/source=`implicit_archive`,
`promote_candidate`=+0.7/`implicit_promote`, `link_capability`=strength
clamped [0,1]/`implicit_link`). Wired in `api/routes/archive.py` +
`candidates.py` + `capability_links.py`. Closes the loop where v1.4 EWMA
stayed at 0 because `record_usage_feedback` was almost never called.
`MEMORY_IMPLICIT_FEEDBACK_ENABLED=true`.

**v2.2 pending_review envelope** — every `memory_get_context` envelope
injects a `<pending_review>` XML block when `decision_candidates` or
`insight_candidates` are pending. Each `<ref>` carries `id`, `kind`,
`title`, `theory_id` (for decision candidates) — every field an agent
needs to call promote / reject without a separate
`/memory/list_candidates` round-trip. Data-driven: block appears only
when source rows exist.

**v2.3 trigger-on-traffic sentinel scheduler** — every `get_context`
triggers a background sentinel pass when overdue. Per-workspace
`threading.Lock` (`maintenance/sentinel_lock.py`) prevents duplicate
concurrent daemons. Hub-mode aware: `db_path` resolved from the
request-scoped connection via `PRAGMA database_list`, not the singleton
`settings.db_path`. `MEMORY_SENTINEL_AUTORUN_HOURS=6.0` default.

### Architecture chokepoint

`apply_post_build_hooks()` in `api/routes/context_post_build.py` is the
single entrypoint for the four context post-build hooks (behavior_apply
tracking, last_retrieved stamping, sentinel scheduler,
pending_review injection). Both the HTTP route
(`api/routes/context.py`) and the MCP stdio local fallback
(`mcp/stdio_handlers_episodes.py`) call it. **Pre-fix MCP-only
deployments silently lost v1.5 / v1.6 / v2.2 / v2.3** on every read.

### Calibration evidence

Replayed 1370 audit_log + capability_links rows from a real copyBot
workspace into 158 implicit feedback rows; 95% rank churn (61 / 64 active
decisions changed position); high-EWMA cohort rose +0.84 places, low-EWMA
cohort dropped 26 places, biggest faller -51 positions for a
high-importance decision with zero operator interaction. Half-life sweep:
identical results across {1, 3, 7, 14, 30, 60} days because backdated
feedback spans only 5 days — keep default 14d, re-sweep after 30+ days
of live writes. Regression injection: archived 1 of 3 active decisions,
sentinel detected delta +1 with no spurious failures.

Reproduce on any post-1.4 workspace via
`scripts/calibration/{replay_implicit_feedback,ab_compare_decisions,
halflife_sweep,regression_injection}.py` (each takes
`--db <path> --workspace <id>`). Full report:
`docs/V1_1_0_CALIBRATION.md`.

### Hardening (post-ship operational fixes folded into 1.1.0)

The 1.1.0 ship surfaced three operational gaps observed during real
deployment. All three landed in this release:

* **MCP function-call markup guard.** When an agent invoked a
  `memory_*` MCP tool with its own function-call boundary tags
  (`</decision_text>`, `<parameter name="...">`, `</invoke>`)
  embedded in the textual content of a parameter, that markup got
  persisted verbatim. New `redaction/mcp_markup.py:strip_mcp_markup`
  truncates at the first marker; idempotent on clean input.
  Pydantic `SafeText` / `SafeTextOptional` annotations apply the
  strip via `AfterValidator` to every text field on every write
  surface (`decisions`, `episodes`, `theories`,
  `behavior_instructions`, `concepts`, `insights`, `roles` /
  `skills` / `playbooks`). Strict markers only — generic angle
  brackets pass through untouched. One-shot cleanup tool
  `scripts/repair_text_artifacts.py` walks every text column and
  applies the same strip + recovers the leaked rationale block.

* **UserPromptSubmit hook FTS fallback.** Pre-hardening the
  auto-injection hook was HTTP-only: if the service at
  `127.0.0.1:8765` was down, every prompt got an empty notice and
  the agent ran blind. New `scripts/inject_memory_fts_fallback.py`
  opens SQLite directly, runs FTS on `chunks_fts` plus
  structured-section reads from `core_memory` /
  `behavior_instructions` / `decisions`, renders a minimal envelope
  in ~30ms (no embedding load — would be 2-3s cold start, unaccept-
  able per-prompt). Hook now degrades HTTP → FTS → notice instead
  of HTTP → notice. MCP stdio already had a similar fallback; the
  two surfaces are now symmetric.

* **Hub-mode dispatch on legacy-schema DBs.** The HTTP service in
  hub mode routes per-call via `X-Memory-DB-Path` header. If the
  hook's cwd doesn't match any registered project root, it
  auto-bootstraps a global workspace at `~/.agent_memory/global/` —
  which may have only v1.0.x migrations applied. Three v1.5 / v1.6 /
  v2.2 post-build hooks now catch `sqlite3.OperationalError` and
  degrade to a no-op when the column / table is missing
  (`pending_review.load_pending_review`,
  `last_retrieved_tracker._update_kind`,
  `behavior_apply.mark_behavior_instructions_applied`). Hub mode
  serves correctly on legacy DBs — features that depend on new
  schema simply skip, which is the correct semantics.

### Quality gates

* `pytest -q` — 655 passed (was 491 in 1.0.3 baseline; +164).
* `ruff check` + `ruff format --check` — clean across 664 files.
* `mypy src` — strict, 0 issues across 432 source files.
* `python scripts/check_sloc.py --enforce` — every `src/**/*.py` ≤ 150 SLOC.
* Crash test (`scripts/crash_test`, 26 phases / 122 assertions) — PASS.

### Operations

`docs/OPERATIONS.md` (new) — operator runbook covering upgrade
workflow (restart MCP server / HTTP service / verify migrations),
service auto-start options (Task Scheduler vs Startup folder vs
manual), hook fallback chain, hub-mode + legacy-DB behaviour,
troubleshooting common failure modes, workspace lifecycle.

### Upgrade path

Migrations 0020-0025 apply automatically on first connection
(`db/migrations.py:apply_migrations`). New columns get neutral defaults
(`feedback_ewma=0.0`, `last_retrieved_at=NULL`, `usage_count=0`, etc.) so
retrieval ranking stays unchanged until the new write paths populate them.
Backwards-compatible: older code ignores new columns. To restore the
v1.0.x baseline, set the corresponding env var to `false` / `0.0` in
`.env` — `tests/invariants/test_v2_parity.py` guarantees byte-equivalence.

---

## 1.0.3 — 2026-04-30

**Patch.** Idempotent agent-contract sync.
`scripts/setup_agent.py:upsert_contract` is now byte-stable across reruns:
`render_contract_block()` produces the same canonical block whether the
file is being created or updated, and the end-marker search uses `rfind`
so the replaced span runs from the FIRST `:begin` to the LAST `:end`. A
hand-broken anchor file with stray duplicate `:end` markers is healed in
a single sync pass instead of silently accumulating drift.

## 1.0.2 — 2026-04-29

**Patch.** Single-source agent contract.
`docs/AGENT_CONTRACT.md` is now the canonical body for the agent operating
contract. `CLAUDE.md` and `AGENTS.md` carry the same body verbatim between
`<!-- agent-memory-lite-contract:begin/end -->` markers. CI runs the same
sync and `git diff --exit-code -- CLAUDE.md AGENTS.md`, so any direct edit
to the marker block in the anchor files (without syncing the canonical)
fails CI.

## 1.0.1 — 2026-04-28

**Patch.** UI live-write refresh fix + action-colored spokes.

* `/ui` no longer goes stale when a new row is written — `graph_delta`
  handler invalidates the per-family detail cache and re-fetches if the
  inspector is currently open on that family.
* Spoke + object node tint encodes the action: created / upserted /
  restored = green, pinned = yellow-green, unpinned = amber, archived /
  superseded = red-orange, deleted / rejected = red, reads keep the
  family hue.

## 1.0.0 — 2026-04-26

**Initial stable release.**

* 18+ persistence kinds (episodes, chunks, files, decisions, theories,
  experiments, snapshots, research_insights, domain_concepts,
  agent_roles / skills / playbooks, capability_links,
  behavior_instructions, core_memory, task_state, procedural_rules,
  entities, facts, audit_log, memory_candidates, maintenance_events,
  memory_state_snapshots, vector_index_metadata, memory_usage_feedback,
  workspace_manifest, workspace_meta).
* RRF fusion of FTS BM25 + vector cosine, graph walk for entity facts,
  token-budget cap, discover-then-fetch index blocks, pinned-first
  ordering for decisions / behavior_instructions / core_memory.
* Operator surface: pin / archive / what_references / list_audit /
  snapshot_save+list+diff / review_queue / compact_trigger; integrity
  audit, hygiene report, quality gate, candidate triage.
* Hub mode + asymmetric isolation: one service serves many projects via
  `~/.agent_memory/workspaces.json`; reads stay loose, writes stay strict
  per-project.
* Live observatory at `/ui` with burst-coalesced animation cycles.
* Memory-quality features (env-flagged, **off** by default in 1.0.x —
  flipped to default ON in 1.1.0): episode dedup, confidence decay, auto
  conflict detection, token-aware compaction watchdog.
