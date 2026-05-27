# V3 Schema — one-page reference

Source of truth: `migrations/0001_init.sql`, a squashed canonical v3 schema.
Fresh databases start directly on the v3 table set; existing databases that
already applied the historical chain keep working because migrations are
tracked forward-only in `schema_migrations`.

## Design principles

1. **SQL is source of truth.** SQLite (WAL + FTS5) + LanceDB (vectors).
   No markdown-as-truth — markdown lives only inside `skills.body_md` /
   `behaviors.rule` columns, fetched on explicit invoke.
2. **Compact projections by default.** Every retrieval path returns a
   `gist` / `*_one_line` / `*_short` field of ~20-40 tokens. Full body
   is opt-in via `memory_get(id, fields=[...])`.
3. **`gist` columns persisted on write.** No on-the-fly summarization at
   read; the writer (or background LLM pass) populates the gist column
   once.
4. **Versioning replaces hard candidate gate.** Every mutation snapshots
   to `versions` table. Rollback writes historical content as a new
   version. Candidates remain only for the correction loop.

## Tables by group

### Core content
- `episodes` — append-only audit; `gist` ≤30 tokens.
- `decisions` — architectural choices; `gist` for compact projection,
  `references_json` (v1.3) for file/symbol scopes, `pinned`,
  `supersedes_decision_id`.
- `theories` — claims + predictions; `gist`, inline `evidence_count` +
  `evidence_strength`, `validation_criteria_json`,
  `dependent_decision_ids_json`.
- `theory_evidence` — FK to theories; kept as separate rows.
- `behaviors` — operator-pinned rules (v3: merges v2 `behavior_instructions`,
  `core_memory`, `procedural_rules` via `kind` column); `rule_one_line`
  for projection, `pinned`, `applies_to_json`.
- `skills` — operating procedures (v3: merges v2 `agent_roles`,
  `agent_skills`, `agent_playbooks` via `subtype` column);
  `when_to_use_short` for projection, `body_md` for invoke,
  v1.5 maturity counters.
- `concepts` — domain vocabulary (renamed from `domain_concepts`);
  `definition_one_line` for projection.
- `tasks` — task state (renamed from `task_state`); `goal_one_line`.

### Research layer
- `snapshots` — research datasets (renamed from `memory_snapshots`).
- `experiments` — planned tests (renamed from `research_experiments`).
- `experiment_results` — measured outcomes.
- `insights` — distilled lessons (renamed from `research_insights`);
  `gist`.

### Code memory substrate
- `files` — file metadata.
- `chunks` — chunked content for retrieval; `gist`, `symbol_kind` /
  `qualified_name` / `parent_qualified_name` for compact code-aware recall.
- `chunks_fts` — FTS5 virtual table (+ `gist` searchable).
- `code_digests` — per-file pre-digested knowledge (renamed from
  `file_digests`); `purpose_short` (≤30 tokens), `top_symbols_json`,
  `top_callers_json`, `top_callees_json`, `pagerank`. Built by
  background `digest_worker.py` daemon.
- `symbol_edges` — hard graph (calls, imports, extends, implements).
- `symbol_versions` — signature history used by code-memory maintenance.
- `soft_edges` — heuristic co-change / co-reference signals (EWMA weight).

### Coordination + review
- `capability_links` — target ↔ role/skill/playbook explicit relations.
- `candidates` — single review queue for correction, decision, insight,
  theory-proposal, and predictive-warning candidates.

### Audit + maintenance
- `audit_log` — every write, including `agent_id`.
- `versions` — **NEW v3.** Immutable rollback history per target.
  Replaces candidate hard-gate for direct edits.
- `maintenance_events` — substrate issues; `recurrence_count` +
  `first_seen_at` + `last_seen_at` for dedup-and-increment.
- `retrieval_sentinel_results` — watchdog per-case verdicts.
- `memory_usage_feedback` (+ summary view) — usefulness signals.

### Infrastructure
- `workspace_meta` — per-workspace key/value config.
- `workspace_manifest` — workspace identity, schema_version=`v3.0.0`.
- `vector_index_metadata` — LanceDB provider/dim/backend tracking.

### Legacy graph (kept for compat; review for removal v3.1)
- `entities` — typed canonical names.
- `facts` — subject-relation-object triples with valid_from/valid_to.

## What changed vs v2

| Change | Reason |
|---|---|
| `task_state` → `tasks` | Naming consistency. |
| `domain_concepts` → `concepts` | Same. |
| `research_experiments` → `experiments`, `research_insights` → `insights`, `memory_snapshots` → `snapshots` | Drop `research_` / `memory_` prefix. |
| `memory_candidates` → `candidates` | Same. |
| `file_digests` → `code_digests` + structured projection cols | More code-agent specific; powers projection-by-default. |
| `behavior_instructions` → `behaviors` + merged `core_memory` + `procedural_rules` | Three names for the same thing collapsed. |
| `agent_roles` + `agent_skills` + `agent_playbooks` → `skills` with `subtype` | Anthropic Skills pattern: dynamically loaded body on invoke. |
| `+ gist / *_one_line / *_short` columns | Compact projections by default; ≤30-token retrieval hits. |
| `+ versions` table | Immutable rollback history; replaces candidate hard-gate. |

## Token-cost contract per kind (compact projection)

| Kind | Default projection fields | Token target |
|---|---|---|
| Decision | id, title, status, gist, supersedes, valid_from | ~25 |
| Theory | id, claim, status, evidence_count, confidence | ~20 |
| Behavior | id, name, rule_one_line, applies_to_csv, pinned | ~30 |
| Skill | id, name, when_to_use_short, body_token_count | ~20 |
| Episode | id, ts, source_type, gist | ~15 |
| Concept | id, name, definition_one_line, aliases_csv | ~20 |
| Code digest | file_path, purpose_short, top_symbols_csv (top 5) | ~40 |
| Task | id, goal_one_line, status, next_action, blockers_count | ~25 |

`memory_brief(workspace, task?)` composes ≤500 tokens from these
projections across kinds: identity (100) + pinned behaviors (120) +
top-5 decisions (130) + state (60) + top-10 code-digest projections (90).

## Migration path

The active path is now a single canonical init migration. Historical cutover
files were squashed into `0001_init.sql`, so new workspaces only create the v3
tables. v3 is the only schema path.

parity report green.
