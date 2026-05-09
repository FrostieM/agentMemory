# Post-v2.0 roadmap — code-memory polish phase

## Context

The V1.4 → V2.0 roadmap shipped in 8 releases (v1.4.0 → v2.0.0). The
substrate is complete: symbol chunks across 7 languages, hard graph
edges, cross-file resolver, version history, breaking-change
detection, soft graph, active-edit registry, file digests, dashboard.

The four items below are NOT blockers — v2.0 closes the original
"team project, multiple AIs, see conflicts and dependencies" ask.
They are quality / parity / depth improvements that sharpen the
existing surface.

After all four ship, we move to a **fix-and-polish phase**: edge
cases, performance, docs, real-world calibration on the workspace
data accumulated by then.

**Versioning note.** Each of the five steps is recorded as a patch
within the v2.1 series (v2.1.1, v2.1.2, v2.1.3, v2.1.4, v2.1.5)
rather than as separate minor versions. Operator preference: the
v2.0 substrate is complete; everything in this roadmap is parity /
depth / polish on top of that substrate, not new substrate. Strict
semver would call new endpoints a minor bump, but the operator's
read is "this is all v2.1 series, recorded as patches". The
``__version__`` and ``pyproject.toml`` strings will follow that
convention; no v2.1.0 placeholder release.

## Goals

1. Bring tree-sitter languages to feature parity with Python on
   edge extraction (extends, decorated_by, implements).
2. Make the dashboard visual — interactive graph rendering replaces
   the table-only v2.0 layout for topology questions.
3. Replace heuristic narrative with LLM-coherent narrative when
   Ollama is available.
4. Populate the third soft-edge kind (`similar_signature`) so
   "find similar code" lookups have real signal.
5. Polish + fix everything once the surface is stable.

## Non-goals

- Cross-workspace graph traversal (out of scope; each workspace
  stays hermetic, established invariant).
- Cloud-LLM enrichment (local-only policy, hard rule).
- Real-time graph streaming (the v2.0 polling cadence is enough
  for human review; LLM-driven agents poll on demand).
- New programming languages (still 8 supported; adding ruby /
  kotlin / php is its own follow-up).

---

## Phase order + version targets

| Version | Scope | Effort | Why this order |
|---|---|---|---|
| **v2.1.1** | Tree-sitter extends / decorated_by parity | 1 day | Cheapest, closes real parity gap, unblocks Java / C# / Rust users immediately. |
| **v2.1.2** | D3.js graph visualization | 1.5 days | Highest "wow" payoff. Makes v1.4-v2.0 substrate visible to the operator. |
| **v2.1.3** | LLM-enriched narrative via Ollama | 1 day | Adds depth to dashboard. Requires Ollama; falls back to heuristic. |
| **v2.1.4** | `similar_signature` soft edges | 1 day | Most speculative — needs real use case. Ship last so we can calibrate on actual workspace data. |
| **v2.1.5** | **Polish + fix phase** | 2-3 days | Edge cases, perf, docs, calibration on workspace data accumulated by then. |

**Total**: 5-7 calendar days.

---

## v2.1.1 — Tree-sitter parity (extends / decorated_by / implements)

### Problem

Python emits 5 edge kinds via stdlib AST (calls, imports, extends,
decorated_by, instantiates). The other 7 languages emit only 3
(calls, imports, instantiates). Java / C# inheritance trees, Rust
trait implementations, TS framework decorators are invisible to
`graph_neighbors`.

### Scope

- Extend ``extraction/symbol_edges_ts_decls.py`` with three new
  per-language tables:
    - ``EXTENDS_NODES`` — class-heritage / superclass / base_class_clause / impl_item
    - ``IMPLEMENTS_NODES`` — implements_clause / super_interfaces / base_list (interface part)
    - ``DECORATOR_NODES`` — decorator / attribute / annotation / attribute_item

- Extend ``extraction/symbol_edges_ts.py`` walker:
    - When entering a class / struct / interface node, scan its
      heritage children for extends/implements targets and emit
      one edge per base.
    - When the parent of a function/class/method declaration has a
      preceding decorator/annotation, emit a `decorated_by` edge.

- Per-language coverage targets (drop one if the grammar makes it
  awkward):

| Language | extends | implements | decorated_by |
|---|---|---|---|
| JavaScript | yes | n/a | n/a (no native decorators) |
| TypeScript | yes | yes | yes |
| Go | n/a (composition not inheritance) | n/a | n/a |
| Rust | yes (impl Trait) | yes (trait bounds) | yes (#[attribute]) |
| Java | yes | yes | yes (annotations) |
| C++ | yes (base_class_clause) | n/a | yes (attributes) |
| C# | yes | yes | yes (attributes) |

### Tests (~12 new)

- Per-language unit tests in
  ``tests/unit/extraction/test_symbol_edges_ts.py`` — extend
  existing test pattern with one new test per (language, edge
  kind) combination that the language supports.
- Skip tests on missing grammars (existing pattern).

### SLOC budget

| File | Net add |
|---|---|
| ``extraction/symbol_edges_ts_decls.py`` | +60 |
| ``extraction/symbol_edges_ts.py`` | +50 (split helper if over 150) |
| Tests | +200 |

### Ship-stop gate

- All gates green (SLOC, mypy, ruff, format).
- Existing tests still pass — no regression on calls/imports.
- 12 new tests pass.

---

## v2.1.2 — D3.js graph visualization

### Problem

`/ui/code` is tables. Tables are great for counts and lists, bad
for topology. "Show me what depends on X" is unreadable as a list
of 50 edges.

### Scope

- New endpoint **`GET /memory/code_graph`**:
    - Params: `workspace_id`, `center_qualified_name?`,
      `depth=2`, `max_nodes=200`, `edge_kinds[]?`
    - When `center` is set: BFS up to `depth` hops from that
      symbol, returning nodes + links subgraph.
    - When `center` is absent: top-K most-connected symbols (by
      inbound + outbound edge count) up to `max_nodes`.
    - Response: `{ nodes: [{id, qualified_name, language, kind,
      file_path, degree}], links: [{source, target, edge_type,
      weight}] }`

- New static page **`/ui/graph.html`**:
    - D3.js force-directed layout (D3 v7, vendored as
      ``ui/d3.v7.min.js`` — ~85kb).
    - Node color by language, size by degree, hover for full
      qualified_name + file_path.
    - Click a node → fetch `/memory/code_graph?center=X&depth=2`
      and re-render.
    - Edge style by edge_type (calls=grey solid, extends=blue
      bold, imports=dashed, decorated_by=orange, etc.)
    - Search box: type qualified_name → re-center.
    - Zoom + pan + reset button.

- Wire `/ui/graph` into `ui.py` route alongside `/ui/code`.
- MCP tool `memory_code_graph` mirroring the endpoint.

### Tests (~6 new)

- E2E: ingest two files with calls between them →
  `/memory/code_graph?center=Foo` returns Foo + its callers/callees
  with correct depth limit.
- E2E: `max_nodes` cap respected.
- E2E: `/ui/graph` HTML page served.
- Smoke: page contains expected D3.js bootstrap code.

### SLOC budget

| File | Net add |
|---|---|
| `api/routes/code_graph.py` | ~120 |
| `api/routes/code_graph_bfs.py` (helper if over 150) | ~60 |
| `mcp/tools_code_graph.py` + handler | ~80 |
| `ui/graph.html` | ~400 (HTML/JS, not Python) |
| `ui/d3.v7.min.js` | vendored 85kb |
| Tests | +150 |

### Ship-stop gate

- Endpoint returns < 1s on a 1000-symbol workspace.
- Page loads + renders an actual graph in headless test (we
  already have Playwright in p24_ui_browser crash-test phase —
  extend it to assert `<svg>` exists with > 0 `<circle>` nodes).
- All gates green.

---

## v2.1.3 — LLM-enriched narrative via Ollama

### Problem

`file_digests.narrative` is a one-line heuristic
("Contains: 1 class, 2 functions. Edges: 5 inbound."). Useful but
doesn't answer "what does this module DO".

### Scope

- New helper `extraction/file_narrative_llm.py`:
    - Takes file's qualified_names, top inbound/outbound edges,
      first-line signatures of public functions, file path,
      language.
    - Calls local Ollama via the existing
      `extraction/llm_extractor.py` infrastructure.
    - Returns a 2-3 sentence coherent paragraph.
    - Times out at 10s; falls back to heuristic narrative on
      failure / Ollama unavailable.

- Wire into `ingestion/file_digest_builder.py`:
    - When `MEMORY_LLM_NARRATIVE_ENABLED=true` (default false —
      conservative, opt-in like v1.8 reflective compaction)
      AND Ollama is reachable AND file has ≥3 symbols → use LLM
      enrichment.
    - Otherwise heuristic.

- Cache: don't re-run LLM if `content_hash` unchanged AND
  narrative already populated.

- New env flag block (mirror v1.8 reflective compaction pattern):
    - `MEMORY_LLM_NARRATIVE_ENABLED` (default false)
    - `MEMORY_LLM_NARRATIVE_MIN_SYMBOLS` (default 3)
    - `MEMORY_LLM_NARRATIVE_TIMEOUT_SEC` (default 10)
    - `MEMORY_LLM_NARRATIVE_MAX_INPUT_CHARS` (default 4000)

### Tests (~5 new)

- Unit: prompt assembly given fixture file + edges.
- Unit: graceful fallback when Ollama unreachable (mocked).
- E2E: with flag off, narrative matches heuristic exactly (parity
  invariant).
- E2E: with flag on + mocked Ollama returning canned text,
  narrative contains the canned text.
- E2E: re-ingest with same content hash + LLM-narrative already
  set → no re-call.

### SLOC budget

| File | Net add |
|---|---|
| `extraction/file_narrative_llm.py` | ~100 |
| `ingestion/file_digest_builder.py` | +20 |
| `config/settings.py` | +15 (4 env flags) |
| Tests | +180 |

### Ship-stop gate

- Flag-off parity test passes byte-for-byte against pre-v2.3
  digest output (no churn).
- LLM call respects timeout (verified by injecting a slow mock).
- All gates green.

---

## v2.1.4 — similar_signature edges in soft-graph

### Problem

`soft_edges` schema supports `similar_signature` but the pipeline
never populates it. The use case ("agent rebuilt fetch_users —
auto-suggest applying to fetch_orders") has no signal today.

### Scope

- New helper `extraction/signature_similarity.py`:
    - Token-based MinHash on `signature_text` (split on
      whitespace + punctuation).
    - 64-bit MinHash signature; Jaccard estimate via signature
      hamming distance.
    - Threshold: emit edge when estimated Jaccard ≥ 0.7
      (configurable).

- Wire into `ingestion/file_persist_versions.py`:
    - When a new version is recorded, hash its signature.
    - Compare against last 100 distinct signatures in the same
      workspace (capped LRU); emit `similar_signature` soft edge
      when over threshold.
    - Bidirectional emission (mirror v1.7 co_changed behavior).

- Bounded scan: `MEMORY_SIMILAR_SIG_SCAN_LIMIT` (default 200) to
  prevent O(N²) growth on large workspaces. LSH index is a
  follow-up patch optimization; not in v2.1.4.

- Env flag: `MEMORY_SIMILAR_SIG_ENABLED` (default true since the
  cost is bounded).

### Tests (~6 new)

- Unit: MinHash deterministic + symmetric.
- Unit: identical signatures → Jaccard 1.0.
- Unit: completely different signatures → Jaccard ~ 0.
- E2E: ingest two functions with similar signatures →
  `soft_neighbors` returns `similar_signature` edge between them.
- E2E: dissimilar signatures → no edge.
- E2E: scan limit respected.

### SLOC budget

| File | Net add |
|---|---|
| `extraction/signature_similarity.py` | ~120 |
| `ingestion/file_persist_versions.py` | +30 |
| `config/settings.py` | +10 |
| Tests | +180 |

### Ship-stop gate

- Threshold calibrated against the workspace's existing code so
  positive-rate is "useful but not noisy" (target: 5-15
  similar-signature edges per 100 symbols).
- All gates green.

---

## v2.1.5 — Polish + fix phase

### What this phase IS

Once v2.1 → v2.4 ship, we turn off feature work and run a
calibration / fix sweep on the substrate accumulated in real
workspaces (agent-memory-lite + copyBot). The goal is:

1. **Calibrate thresholds** — `similar_signature` Jaccard cutoff,
   `MEMORY_SIMILAR_SIG_SCAN_LIMIT`, breaking-change days window,
   active-edit TTL — based on what the data shows.

2. **Fix edge cases the test suite missed** — e.g. multi-line
   Python signatures with type annotations split across lines,
   C++ template-heavy functions, TypeScript generic type params
   in qualified_name reconstruction.

3. **Performance pass** — profile `code_overview` on a
   1000+ symbol workspace; index any new SQL hotspots; consider
   LSH index for `similar_signature` if scan cost matters.

4. **Documentation** — update `docs/AGENT_CONTRACT.md` with the
   v2.x tools (currently only references through v1.10); write
   a `docs/CODE_MEMORY_GUIDE.md` for operators ("here's what each
   tool does, here's when to use which").

5. **Real-world calibration report** — replay v2.0 dashboard
   queries against a populated copyBot workspace; capture metrics
   (cache hit rate, query latency, edge-density distribution);
   commit as `docs/V2_CALIBRATION.md` like
   `docs/V1_1_0_CALIBRATION.md`.

### What this phase IS NOT

- New schema migrations (substrate is settled).
- New endpoints (surface is settled).
- New language support.
- LLM-anything beyond v2.3 narrative.

### Inputs

- Telemetry from `/memory/telemetry` accumulated since v1.3
  per-agent split (already in production).
- `audit_log` patterns: which tools fire most, which fail.
- Hygiene reports from existing copyBot workspace.
- Operator complaints accumulated during v2.1 → v2.4 (write them
  down as we go, not from memory at end).

### Outputs

- ~5-10 small commits each touching one concern.
- One `docs/V2_CALIBRATION.md` report.
- One operator guide doc.
- A clean `git log` between v2.1.4 and v2.1.5 that an outside
  reader can follow.

### Ship-stop gate

- Every fix lands behind a test that would have caught the
  underlying bug.
- Calibration doc has actual measurements, not guesses.
- All gates green at every commit.

---

## Cross-cutting rules (apply to v2.1.1 — v2.1.4)

1. **No version-pump-and-dump.** If an internal review during
   v2.1.X uncovers a small bug, fix it inside the SAME release —
   don't spin yet another patch just for one fix. Operator
   preference established earlier in v1.4.0.

2. **CI is the source of truth.** Pre-push crash test is local-only
   and does NOT cover SLOC / mypy / ruff / contract sync. After
   every push, verify GitHub Actions via
   ``gh run watch <id> --exit-status``. Locked by the v1.4.0
   behavior_instruction `verify-ci-actions-after-push`.

3. **SLOC ceiling stays at 150** (CI rule, not negotiable). Split
   into helpers when approaching the limit; don't fight it.

4. **Tests are paired.** Every new module → one test file. Every
   bug fix → one test that would have caught it.

5. **Local-only.** No new cloud SDKs. The v2.3 LLM narrative is
   Ollama-only.

---

## Acceptance criteria for the whole post-v2 phase

When all five patch releases (v2.1.1, v2.1.2, v2.1.3, v2.1.4,
v2.1.5) have shipped:

- [ ] Tree-sitter languages emit ≥ 4 edge kinds (calls, imports,
      instantiates + at least one of extends/decorated_by per
      language that supports it).
- [ ] D3.js dashboard renders a real graph for a workspace with
      ≥ 100 symbols in < 1 second.
- [ ] `MEMORY_LLM_NARRATIVE_ENABLED=true` produces qualitatively
      better narratives (operator review of 10 random files).
- [ ] `similar_signature` edges populated for the agent-memory-lite
      workspace at the calibrated threshold; positive rate
      between 5% and 15% of new versions.
- [ ] Calibration doc published with actual numbers.
- [ ] Operator guide published.
- [ ] No new bugs introduced (existing test suite stays at ≥ 843
      pass; new releases add tests monotonically).
- [ ] Every release CI green; every release tagged.

---

## Why this order is the right order

We ship parity (v2.1.1) first because it's the smallest gap to
close and gives the best ratio of "users helped per SLOC".
v2.1.2 is the big visual unlock that makes the substrate
operator-legible — which in turn makes calibrating thresholds in
v2.1.5 actually possible (you can see the soft-edge density on a
graph; you can't on a JSON dump). v2.1.3 + v2.1.4 are independent
depth additions; we do v2.1.3 before v2.1.4 because LLM narrative
has higher upside and similar_signature is the most speculative
item — getting it last means we calibrate on real data, not
guesses.

Polish (v2.1.5) at the end is critical: without it we ship four
features and three of them have ad-hoc thresholds nobody validated.
v2.1.5 turns "we shipped it" into "we know it works".

## Tracking

Each patch release follows the established release flow from the
v1.4 → v2.0 roadmap:

1. Implement scope; keep all gates green at every step.
2. Bump ``__version__`` and ``pyproject.toml`` to the patch.
3. Append CHANGELOG entry (one section per patch, like the
   v1.5.1 / v1.5.2 entries did).
4. Commit + tag ``v2.1.X``.
5. ``git push origin main`` (pre-push crash-test runs).
6. ``git push origin v2.1.X``.
7. ``gh run watch <id> --exit-status`` to confirm CI green —
   locked by the v1.4.0 ``verify-ci-actions-after-push``
   behavior_instruction.

When the whole v2.1.x series finishes, the final acceptance-
criteria checklist (above) gates a v2.2.0 minor bump if and only
if we have a NEW substrate-level reason for one. Until then we
stay on v2.1.x.
