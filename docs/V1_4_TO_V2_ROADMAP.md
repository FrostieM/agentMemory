# Roadmap: Architectural Code Memory for Multi-Agent Teams (v1.4 → v2.0)

**Status:** approved 2026-05-09; foundations in v1.3.0; Phase 1 starts next.
**Owner:** operator + Claude / Codex collaboration.
**Scope target:** copyBot team-mode use case where multiple AI agents
work in parallel on a polyglot codebase (Python + TS + C++ + C#) and
need memory to be an active architectural layer rather than a passive
text corpus.

This document is the binding plan for releases v1.4.0 through v2.0.
Every release before v2.0 is independently shippable and produces
measurable value on its own. Stop / pivot / re-prioritise after any
release if telemetry shows the next phase is no longer the right
investment.

## 1. Problem this solves

Today, multi-agent work on copyBot exhibits five recurring failures
that text-only memory cannot prevent:

1. **Architecture drift.** Agent-A writes decision `dec_x`. Two weeks
   later agent-B writes `dec_y` that contradicts it. Both surface in
   `<active_decisions>`; nothing flags the contradiction.
2. **Invisible caller breakage.** Agent-A renames or re-signatures a
   function. Agent-B writes code calling the old name in the next
   session. CI catches it. Memory should have caught it pre-edit.
3. **Logic duplication across languages.** Agent-A writes
   `validate_email_addr` in Python; agent-B writes `isValidEmail` in
   TS two weeks later. Different edge cases. Memory should have
   surfaced the existing implementation.
4. **Stale references after refactoring.** A symbol is removed; 14
   memory items still reference it (decisions, theories, BIs). Silent
   debt — no operator-visible signal.
5. **Concurrent edit conflicts.** Agent-A is editing `selector.py`;
   agent-B reads stale version, opens incompatible PR. No
   coordination primitive between agents.

The root cause is that memory currently stores **text about code**,
not **structure of code**. We have decisions and chunks; we don't
have a queryable graph of who-calls-whom or who-references-whom.

## 2. Goals / non-goals

### In scope
- Symbol-level indexation (functions / classes / methods / structs /
  interfaces / enums) for the top-7 languages: Python, JS, TS, Go,
  Rust, Java, C++ / C#.
- Hard dependency graph using existing `facts` table:
  `CALLS`, `IMPORTS`, `EXPORTS`, `EXTENDS`, `IMPLEMENTS`, `REFERENCES`,
  `INSTANTIATES`, `DECORATED_BY`, `ASSERTS_BEHAVIOR_OF`.
- Soft (vector) graph endpoints over symbol embeddings:
  `/memory/neighbors` and cross-language clustering.
- Active-edit registry so agents see who is editing what right now.
- Conflict detection in `/memory/explain_diff` (concurrent edits,
  broken signatures, stale references).
- Symbol-level versioning: detect signature change at re-ingest,
  emit maintenance events for affected callers.
- Pre-edit and pre-commit hook integration.
- Per-agent attribution on every graph query (continues v1.3.0
  `X-Memory-Agent-Id` work).

### Explicitly out of scope
- Replacing git, CI, or PR review tools — memory complements them.
- Resolving dynamic dispatch / runtime reflection / template
  specialisation through pure static analysis. Mark uncertain edges,
  do not pretend the graph is complete.
- IDE plugin or language server. Headless service stays headless;
  any IDE integration is a separate project.
- Cross-repo graphs. One workspace = one codebase.
- Code-aware embedding model in v1.4 (optional via env flag, default
  remains the existing e5-small).
- Lock-based coordination between agents. Edit registry is advisory
  only; agents read the signal and decide.
- Automatic merge-conflict resolution.

## 3. Three-layer architecture

```
Layer 4 — Narrative (decisions / theories / insights)
            references symbols explicitly
                ↑                       ↑
                │                       │
Layer 2 — Hard graph         Layer 3 — Soft graph
   facts table:              LanceDB kNN over chunk embeddings
   CALLS / IMPORTS / ...     neighbors(symbol_id, k=10)
   temporal valid_from/to    cross-language clustering
                ↑                       ↑
                └───────────┬───────────┘
                            │
Layer 1 — Symbol chunks (foundation)
   one chunk per function / class / method / struct
   metadata: language, kind, qualified_name, parent, signature,
             docstring, annotations, line range, body hash
   embeddings via tree-sitter parse → chunk text
```

All four layers cooperate: agent asking "what does this symbol do
and who uses it" gets a single response built from all four.

## 4. Multi-agent coordination

Specific to scenario B (team mode):

### Active-edit registry
New table `active_edits` tracks `(workspace_id, agent_id, file_path,
symbol_qualified_name, intent, started_at, expires_at,
last_heartbeat_at)`. Endpoints `/memory/edits/start`,
`/memory/edits/heartbeat`, `/memory/edits/finish`,
`/memory/edits/active`. TTL 30 minutes; heartbeat keeps alive.
Advisory only — no locks.

### Conflict detection in explain_diff
Extends v1.3.0 `/memory/explain_diff` with new fields:
- `active_conflicts`: other agents currently editing files in the
  diff;
- `broken_signatures`: signatures referenced by the diff that have
  changed since the diff was based;
- `stale_references`: symbols deleted but still referenced by memory
  items.

### Memory as cross-agent communication channel
Decisions written by agent-A automatically reach agent-B's pre-edit
envelope when the graph resolves agent-B's target file to symbols
referenced by `dec_x`. No explicit coordination needed; the graph
does the routing.

### Symbol-level versioning
At `ingest_file` re-index, any symbol whose signature hash changed:
- old chunk closed (`valid_to = now`);
- new chunk created with same `qualified_name`, new signature;
- audit event `symbol_signature_changed`;
- maintenance events for callers whose `valid_from` predates the
  change — flagged as `caller_may_be_stale`.

## 5. Data model deltas

### Existing tables extended
- **chunks**: + `language`, `symbol_kind`, `qualified_name`,
  `parent_qualified_name`, `signature`, `docstring`,
  `annotations_json`, `symbol_version`.
- **facts**: kind enum extended with eight code-edge kinds
  (`code_call`, `code_import`, `code_export`, `code_extends`,
  `code_implements`, `code_references`, `code_instantiates`,
  `code_decorated_by`, `test_asserts`).

### New tables
- `active_edits` — multi-agent edit registry.
- `symbol_neighbors` (optional, materialised) — top-k per-symbol
  similarity cache for fast pre-edit lookups.
- `file_digest` — file-level architectural summary (exports,
  imports, purpose-line, last-indexed timestamp).

### Migrations (numbered for forward-only convention)
- `0028_chunks_symbol_metadata.sql`
- `0029_active_edits.sql`
- `0030_symbol_neighbors.sql`
- `0031_file_digest.sql`

## 6. API surface (chronological by phase)

### v1.4.0 — Symbol search
- `POST /memory/find_symbols` — vector + filter search
  (`workspace_id`, `query`, `language?`, `kind?`, `path_prefix?`).
- `GET /memory/symbol?id=...` — fetch one symbol fully.

### v1.5.0 — Hard graph
- `GET /memory/dependents?symbol_id=...&depth=1|transitive`
- `GET /memory/dependencies?symbol_id=...`
- `GET /memory/test_coverage?symbol_id=...`
- `explain_diff` extended with `broken_signatures` and
  `stale_references` (uses graph).

### v1.6.0 — Soft graph + edit registry
- `GET /memory/neighbors?symbol_id=...&k=10`
- `POST /memory/edits/start` / `heartbeat` / `finish`
- `GET /memory/edits/active`
- `explain_diff` extended with `active_conflicts`.

### v1.7.0 — Narrative + file digest
- `GET /memory/symbol_purpose?qualified_name=...` — composed view.
- `GET /memory/file_digest?path=...`
- Refactoring partner finder via soft graph + heuristic clustering.

### v2.0 — Dashboard
- `/ui/architecture` — graph visualisation.
- Conflict tracker UI.
- Refactoring suggestion panel.

## 7. Implementation phases

| Release | Scope                                   | Effort       | Ship-stop gate                                            |
|---------|-----------------------------------------|--------------|-----------------------------------------------------------|
| v1.3.0  | Already done — telemetry + references   | shipped      | —                                                         |
| v1.4.0  | Symbol chunks for 7 languages (no graph)| ~1.5 weeks   | hit-rate stays ≥ baseline; no regressions                 |
| v1.5.0  | Hard graph + dependents endpoint        | ~2 weeks     | dependents endpoint called ≥ 5/day after release          |
| v1.5.1  | Symbol versioning + breaking-change     | ~1 week      | ≥ 1 broken_signature caught in real PR                    |
| v1.6.0  | Soft graph + edit registry              | ~2 weeks     | ≥ 1 concurrent_edit conflict caught                       |
| v1.7.0  | Narrative + file digest                 | ~2 weeks     | symbol_purpose called by agents in pre-edit               |
| v2.0    | Dashboard + multi-agent UI              | ~3 weeks     | operator-survey: dashboard used at least weekly           |

**Total:** ~12 calendar weeks at current cadence. Each release
delivers discrete value. Pivot / pause / abandon possible after any
release based on metrics.

## 8. Index freshness strategy

**Default:** pre-commit hook re-ingests changed files.
- Plus: index always matches HEAD.
- Minus: requires hook discipline; ~200-500ms per file slowdown on
  commit.

**Fallback:** agent calls `ingest_file` after edit as safety net.

**Background scan:** every 6h, check `mtime(file) > last_ingested_at`
for any file. If yes — emit `index_stale` maintenance event so the
operator notices.

## 9. Failure modes + mitigations

| Failure                                          | Mitigation                                       |
|--------------------------------------------------|--------------------------------------------------|
| Tree-sitter mis-parses edge case                 | Token-window fallback; emit maintenance event    |
| False-positive call edges (wrong target)         | Confidence < 1.0 marker on edge; API surfaces it |
| False-negative (dynamic dispatch)                | Chunk flag `has_dynamic_dispatch`; warning in API|
| Stale index because re-ingest didn't run         | 6h background scan emits `index_stale`           |
| `active_edits` not cleaned up                    | TTL 30min + 5min heartbeat-loss expire           |
| LanceDB grows linearly with code                 | Cold-symbol scan; operator can drop unused       |
| Embedding model change invalidates all vectors   | Existing `scripts/reindex_vectors.py` handles it |
| Agent-A and agent-B disagree about architecture  | Memory shows both, surfaces contradiction; operator decides |
| Tree-sitter grammar version drift between installs| Pin exact version in pyproject.toml             |

## 10. Success metrics

### One month after v1.5.0
- `memory_search` hit-rate on code-related queries ≥ 0.85
  (current 1.2.4 baseline ~0.6).
- 0 stale references in `decisions.references_json` (sanity check).
- ≥ 80% of new decisions carry non-empty `references` (currently
  ~30%).
- ≥ 1 `concurrent_edit` conflict caught and resolved without CI
  breakage.
- Telemetry shows agent calls to `/memory/dependents` ≥ 5/day.

### Three months after v1.7.0
- "Accidentally broken" callers in PR review reduced ≥ 50% from
  measured pre-v1.4.0 baseline.
- Cross-language pattern duplication detection catches ≥ 1 case
  per week.
- File digest helps onboard a fresh agent in unfamiliar files
  (subjective; via operator survey).

### Pivot signal — investment is not paying off
- One month after v1.5.0, hit-rate is flat.
- `/memory/dependents` called less than 1×/day.
- Stale-reference detector emits less than 1 event / week (means
  there's no refactoring happening, so the graph buys nothing).

## 11. Open questions (resolve before / during phase)

1. Which code-aware embedding model becomes default by v1.6.0?
   Options: keep e5-small, switch to jina-code-v2 (local), Voyage
   (cloud — would break local-only invariant).
2. AST cache strategy. Re-parsing tree-sitter on every re-ingest is
   ~50ms; cache parsed AST keyed on file content hash?
3. Multi-repo within one workspace. If copyBot grows sub-repos, how
   are they linked without cross-leakage?
4. Conflict resolution semantics. Should agent-B's stale envelope be
   auto-refreshed when agent-A finishes an edit?
5. API stability tier. Which endpoints are committed long-term vs
   experimental?

## 12. Migration / re-ingest

- All migrations 0028-0031 are additive; they do not break existing
  rows.
- Existing `chunks` without `language`/`symbol_kind` continue to work
  via fallback (the same plain text chunking they had before).
- New helper `scripts/memory_reindex_code.py --workspace ...` walks
  every supported file via `ingest_file`, replacing old chunks with
  symbol-level ones. Backup-first invariant preserved.
- Audit log is appended on every refactoring-driven change for full
  history.

## 13. Decisions made by this roadmap

The following are committed and will not be re-debated without
explicit operator override:

- Tree-sitter is the unified parser; per-language parsers
  (libclang, Roslyn, ts-morph) are rejected.
- Three-layer architecture (symbol chunks + hard graph + soft graph)
  with narrative on top is the design. Single-layer alternatives
  (vector-only or graph-only) are rejected because each misses cases
  the other catches.
- Per-agent identity already exists via v1.3.0 middleware; we
  continue that contract.
- Local-only constraint preserved: no cloud embedding / parsing /
  LLM dependency added by this roadmap. Optional cloud is operator
  override only.
- Each phase ships independently; no big-bang merge.

## 14. Next concrete actions

1. **Ship v1.3.0** — current working tree as-is. Foundation for
   everything below.
2. **v1.4.0 Phase 1** — tree-sitter dependency + generic symbol
   extractor + top-7 grammars. Migration 0028. New
   `/memory/find_symbols` endpoint. Tests across all 7 languages.
   Default embedding stays e5-small. ~1.5 weeks.
3. **Measure** for 1-2 weeks. Decide whether v1.5.0 hard-graph is
   still the right next step.
