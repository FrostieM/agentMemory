# agent-memory-lite: functionality review package for analyst

Generated: 2026-04-30  
Repository: `C:\Users\Osino\Desktop\work\agent-memory-lite`  
Purpose: give an analyst a concrete map of the whole memory system, what to evaluate, and how to reproduce typical flows.

## 1. Executive summary

`agent-memory-lite` is a local persistent memory service for AI agents. It is not a chat transcript store only. The current design combines:

- SQLite as the source-of-truth database.
- SQLite FTS (`chunks_fts`) for exact lexical retrieval.
- LanceDB vectors (`vectors.lance`) for semantic retrieval.
- Structured memory tables for decisions, theories, experiments, insights, concepts, roles, skills, playbooks, behavior instructions, candidates, and maintenance events.
- HTTP API on `127.0.0.1:8765`.
- MCP stdio tools for direct agent use.
- Audit, hygiene, watchdog, trust dashboard, backup/restore, and eval scripts.

The intended product behavior is: before important work, the agent retrieves relevant context; after important work, it writes durable memory into the correct object type; maintenance tooling proves that retrieval is still trustworthy.

## 2. Mental model

The memory has two different jobs:

1. **Find facts later.**
   - Use `episodes`, `chunks`, FTS, and vectors.
   - Example: "What happened during the VPS reset?" or "find heap watchdog incident".

2. **Shape future work.**
   - Use `decisions`, `theories`, `experiments`, `insights`, `roles`, `skills`, `playbooks`, and `behavior_instructions`.
   - Example: "Do not reset live data without a backup" belongs in a behavior instruction or decision, not only in an episode.

The key product improvement is that memory is no longer only an archival log. It is a typed working system.

## 3. Storage architecture

### 3.1 SQLite source of truth

SQLite holds the canonical state:

- `episodes`: append-only event log.
- `chunks`: searchable text chunks produced from episodes/files.
- `chunks_fts`: FTS mirror of chunks.
- `decisions`: active/superseded/rejected decisions.
- `theories`, `theory_evidence`: hypotheses and supporting/refuting evidence.
- `memory_snapshots`, `experiments`, `experiment_results`: research workflow.
- `research_insights`, `domain_concepts`: distilled learning and vocabulary.
- `agent_roles`, `agent_skills`, `agent_playbooks`: capability memory.
- `capability_links`: which capability should influence which object.
- `behavior_instructions`: persistent operating/communication rules.
- `memory_candidates`: review queue created by extraction.
- `maintenance_events`: degraded memory substrate events.
- `workspace_manifest`: DB identity and audit metadata.

Analyst check:

```powershell
sqlite3 .agent_memory/memory.db ".tables"
sqlite3 .agent_memory/memory.db "PRAGMA integrity_check;"
```

### 3.2 FTS layer

FTS is for exact term lookup. It should be used when the query contains exact identifiers:

- file paths;
- error strings;
- IDs like `ep_...`, `chk_...`, `dec_...`;
- exact terms such as `heap_watchdog`, `workspace_id`, `paper 504`.

Expected invariant:

```text
chunks(workspace_id=X) == chunks_fts(workspace_id=X)
missing == 0
extra == 0
workspace_mismatch == 0
```

### 3.3 Vector layer

The vector layer stores semantic embeddings in LanceDB. It is used when the query is paraphrased or conceptually related.

Example:

```text
Query: "why did the bot hang?"
Potential chunk text: "event loop stall", "heap pressure", "paper-fast tick stale"
```

FTS may miss that query if exact words differ. Vector retrieval should still return relevant chunks.

Expected invariant:

```text
chunks == vector rows
missing_embedding_ids == 0
missing vector ids == 0
extra vector ids == 0
```

Important nuance: the current system treats chunk IDs as vector row IDs. `embedding_id` must be populated by the current hardening layer, but analyst should verify actual LanceDB row parity, not only infer vector health from a single SQLite column.

## 4. Retrieval architecture

Primary file: `src/agent_memory_lite/retrieval/context_builder.py`

`memory_get_context` builds an XML-like envelope:

```xml
<memory_context>
  <core_memory/>
  <behavior_instructions/>
  <task_state/>
  <active_decisions/>
  <active_theories/>
  <research_agenda/>
  <agent_capabilities/>
  <procedural_rules/>
  <retrieved_facts/>
  <retrieved_chunks/>
</memory_context>
```

Retrieval sources:

- FTS candidates from `retrieval/candidates_fts.py`.
- Vector candidates from `retrieval/candidates_vector.py`.
- Graph/fact candidates from `retrieval/candidates_graph.py`.
- Rank fusion from `retrieval/fusion_rrf.py`.
- Token clipping from `retrieval/token_budget.py`.

Analyst evaluation should verify not only `/memory/search`, but also `/memory/get_context`, because agents mostly consume `get_context`.

## 5. Ingestion flows

### 5.1 Episode ingestion

Endpoint/tool:

- HTTP: `POST /memory/ingest_episode`
- MCP: `memory_ingest_episode`

Purpose: persist an observed event with redaction and chunking.

Example:

```json
{
  "workspace_id": "<workspace_id>",
  "source_type": "agent_action",
  "raw_text": "Fixed API timeout by changing cache refresh path. Verified pytest and /health.",
  "trust_level": "verified_by_tool",
  "importance": 0.7
}
```

Expected output:

- `episode_id`
- `chunk_id`
- `redacted_text`
- `candidates_written`

Evaluation questions:

- Are secrets redacted?
- Does it create a chunk?
- Does FTS find the chunk?
- Does vector retrieval find it via paraphrase?
- Are candidates created only as reviewable candidates, not auto-promoted active decisions?

### 5.2 File ingestion

Endpoint/tool:

- HTTP: `POST /memory/ingest_file`
- MCP: `memory_ingest_file`

Purpose: index a file idempotently by content hash.

Example:

```json
{
  "workspace_id": "<workspace_id>",
  "path": "src/foo.py",
  "content": "def foo(): return 1",
  "language": "python"
}
```

Expected behavior:

- Same content returns skipped/idempotent result.
- Changed content produces updated chunks.
- Search by path should find it.

## 6. Structured memory objects

### 6.1 Decisions

Endpoint/tool:

- `POST /memory/write_decision`
- `memory_write_decision`

Use for committed choices, not hypotheses.

Example:

```json
{
  "workspace_id": "<workspace_id>",
  "title": "Use backup-first repair",
  "decision_text": "Any repair touching real memory DB or vector store requires a backup first.",
  "rationale": "Repair must be reversible and auditable."
}
```

Analyst should check:

- supersedes chains;
- active vs superseded decisions;
- source/rationale quality.

### 6.2 Theories and evidence

Endpoint/tools:

- `POST /memory/write_theory`
- `POST /memory/add_theory_evidence`
- `POST /memory/list_theories`

Use for research claims.

Example:

```json
{
  "workspace_id": "<workspace_id>",
  "title": "Vector retrieval improves paraphrased incident recall",
  "domain": "memory.retrieval",
  "claim": "Paraphrased incident queries should retrieve relevant chunks that exact FTS misses.",
  "mechanism": "Semantic embeddings encode related concepts even when words differ.",
  "predictions": [
    "Exact-token queries pass via FTS",
    "Paraphrase queries pass only when vector index is healthy"
  ],
  "validation_criteria": [
    "At least 5 sentinel paraphrase cases appear in top 10",
    "memory_get_context includes expected chunk at max_tokens >= 2500"
  ],
  "experiment_plan": "Run retrieval sentinel suite with FTS and vector enabled.",
  "status": "testing",
  "confidence": 0.5
}
```

Analyst should check:

- theories have validation criteria;
- rejected theories are preserved;
- evidence has kind: supporting/refuting/mixed/neutral/experiment;
- important theories have capability links.

### 6.3 Snapshots, experiments, results

Endpoint/tools:

- `POST /memory/register_snapshot`
- `POST /memory/write_experiment`
- `POST /memory/add_experiment_result`
- `POST /memory/list_research_agenda`

Use when analysis depends on a database export or reproducible dataset.

Example snapshot:

```json
{
  "workspace_id": "<workspace_id>",
  "snapshot_key": "server_20260430T120000",
  "title": "Server DB snapshot before reset",
  "source": "vps",
  "db_path": "research/db_snapshots/server_20260430T120000.db",
  "table_counts": {"trade_decision_fact": 49452},
  "total_rows": 499141
}
```

Analyst should check:

- table counts are captured;
- artifact paths exist;
- experiment result updates linked theory;
- stale experiments appear in hygiene.

### 6.4 Insights and concepts

Endpoint/tools:

- `POST /memory/distill_insight`
- `POST /memory/update_insight`
- `POST /memory/upsert_concept`
- `POST /memory/list_insights`
- `POST /memory/list_concepts`

Use concepts to normalize vocabulary and insights to turn raw episodes into reusable learning.

Example concept:

```json
{
  "workspace_id": "<workspace_id>",
  "name": "retrieval integrity",
  "kind": "term",
  "definition": "Proof that SQLite, FTS, vectors, workspace isolation, and MCP retrieval are consistent enough to trust.",
  "aliases": ["memory audit", "retrieval audit"]
}
```

### 6.5 Roles, skills, playbooks, capability links

Endpoint/tools:

- `POST /memory/upsert_agent_role`
- `POST /memory/upsert_agent_skill`
- `POST /memory/upsert_agent_playbook`
- `POST /memory/list_agent_capabilities`
- `POST /memory/link_capability`
- `POST /memory/list_capability_links`

Purpose: make expertise and workflows first-class.

Example capability link:

```json
{
  "workspace_id": "<workspace_id>",
  "target_type": "theory",
  "target_id": "th_...",
  "capability_type": "skill",
  "capability_name": "Replay and backtest design",
  "relation": "method",
  "rationale": "This hypothesis must be tested by replay before policy changes.",
  "strength": 0.9
}
```

Analyst should evaluate whether roles/skills actually appear in `memory_get_context`, not only whether rows exist.

### 6.6 Behavior instructions

Endpoint/tools:

- `POST /memory/upsert_behavior_instruction`
- `POST /memory/list_behavior_instructions`

Purpose: store persistent operating rules or communication preferences with scope, priority, confidence, and conflict policy.

Example:

```json
{
  "workspace_id": "<workspace_id>",
  "name": "Evidence-first incident reports",
  "kind": "communication_style",
  "scope": "workspace",
  "priority": "user_preference",
  "rule": "When reporting incidents, lead with issue, evidence, fix, and remaining risk.",
  "conflict_policy": "current_user_wins",
  "confidence": 0.95
}
```

Important boundary:

- Generic DB setup must not seed language, style, personality, or project-specific behavior instructions.
- The new neutral seed writes only memory-population skills/playbooks/concepts.

## 7. Candidate-first extraction

Important behavior: extraction candidates are review-first.

Endpoint/tools:

- `POST /memory/list_candidates`
- `POST /memory/promote_candidate`
- `POST /memory/reject_candidate`

Expected behavior:

- Ingestion may create `memory_candidates`.
- Candidates are not active decisions until reviewed.
- Rejected candidates remain as audit evidence.

Analyst checks:

```powershell
python scripts/memory_candidate_triage.py --workspace <workspace_id> --db-path .agent_memory/memory.db --json
```

Key metrics:

- stale candidates;
- high-value unreviewed candidates;
- by status and kind.

## 8. Workspace isolation

Workspace isolation has two layers:

1. Physical DB path:
   - `MEMORY_DB_PATH`
   - `VECTOR_DB_PATH`

2. Logical namespace:
   - `workspace_id`

Project mode should reject accidental `default` writes when configured with `MEMORY_FORBID_DEFAULT_WORKSPACE=true`.

Analyst checks:

```powershell
python scripts/memory_audit.py --workspace <workspace_id> --db-path .agent_memory/memory.db --vector-path .agent_memory/vectors.lance --json
```

Expected:

```text
workspace_pollution.status == ok
default_rows == {}
other_workspace_rows == {}
workspace_manifest.workspace_id == <workspace_id>
```

## 9. HTTP API surface

Primary endpoints found in `src/agent_memory_lite/api/routes`:

### Retrieval and search

- `GET /health`
- `POST /memory/get_context`
- `POST /memory/search`
- `POST /memory/run_evals`
- `POST /memory/compact`

### Ingestion and state

- `POST /memory/ingest_episode`
- `POST /memory/ingest_file`
- `POST /memory/update_task_state`
- `POST /memory/write_decision`

### Candidates and maintenance

- `POST /memory/list_candidates`
- `POST /memory/promote_candidate`
- `POST /memory/reject_candidate`
- `POST /memory/list_maintenance_events`
- `POST /memory/resolve_maintenance_event`
- `GET /memory/hygiene_report`

### Research

- `POST /memory/write_theory`
- `POST /memory/add_theory_evidence`
- `POST /memory/list_theories`
- `POST /memory/register_snapshot`
- `POST /memory/write_experiment`
- `POST /memory/add_experiment_result`
- `POST /memory/upsert_concept`
- `POST /memory/distill_insight`
- `POST /memory/update_insight`
- `POST /memory/list_research_agenda`
- `POST /memory/list_concepts`
- `POST /memory/list_insights`

### Capabilities and behavior

- `POST /memory/upsert_agent_role`
- `POST /memory/upsert_agent_skill`
- `POST /memory/upsert_agent_playbook`
- `POST /memory/list_agent_capabilities`
- `POST /memory/link_capability`
- `POST /memory/list_capability_links`
- `POST /memory/upsert_behavior_instruction`
- `POST /memory/list_behavior_instructions`

## 10. MCP tool surface

Primary MCP tools exposed in `src/agent_memory_lite/mcp/tools.py`:

- `memory_get_context`
- `memory_search`
- `memory_ingest_episode`
- `memory_ingest_file`
- `memory_update_task_state`
- `memory_write_decision`
- `memory_list_candidates`
- `memory_promote_candidate`
- `memory_reject_candidate`
- `memory_write_theory`
- `memory_add_theory_evidence`
- `memory_list_theories`
- `memory_register_snapshot`
- `memory_write_experiment`
- `memory_add_experiment_result`
- `memory_upsert_concept`
- `memory_distill_insight`
- `memory_update_insight`
- `memory_list_research_agenda`
- `memory_list_concepts`
- `memory_list_insights`
- `memory_upsert_agent_role`
- `memory_upsert_agent_skill`
- `memory_upsert_agent_playbook`
- `memory_list_agent_capabilities`
- `memory_link_capability`
- `memory_list_capability_links`
- `memory_upsert_behavior_instruction`
- `memory_list_behavior_instructions`
- `memory_list_maintenance_events`
- `memory_resolve_maintenance_event`

Analyst should verify parity between HTTP and MCP for the important tools:

- same workspace;
- same DB path;
- same recent objects returned;
- similar latency.

## 11. CLI and operational scripts

Key scripts:

- `scripts/setup_agent.py`: configure project/global MCP and contract.
- `scripts/seed_project_memory.py`: seed neutral memory-population helpers only.
- `scripts/bootstrap_db.py`: apply migrations.
- `scripts/reindex_vectors.py`: rebuild vector rows.
- `scripts/memory_audit.py`: read-only integrity audit plus explicit repair flags.
- `scripts/memory_hygiene.py`: content quality report.
- `scripts/memory_watchdog.py`: audit + eval + hygiene recurring check.
- `scripts/memory_trust_dashboard.py`: one-command trust report.
- `scripts/memory_mcp_smoke.py`: fresh-process MCP context smoke.
- `scripts/memory_candidate_triage.py`: candidate backlog review.
- `scripts/memory_backup_restore_check.py`: backup/restore proof.
- `scripts/memory_contract_check.py`: contract docs sanity check.
- `scripts/memory_ci_gate.py`: strict gate for deploy/CI.
- `scripts/run_evals.py`: retrieval/eval suite.
- `scripts/research_status.py`: human-readable research backlog.
- `scripts/ingest_workspace.py`: bulk file indexing.

## 12. Example: first setup for a project

```powershell
cd C:\path\to\project
C:\path\to\agent-memory-lite\.venv\Scripts\python.exe C:\path\to\agent-memory-lite\scripts\setup_agent.py --project . --workspace <workspace_id>
```

Expected effects:

- creates `.agent_memory/memory.db`;
- creates `.agent_memory/vectors.lance`;
- writes `.claude/settings.json`;
- writes `CLAUDE.md` and `AGENTS.md`;
- seeds neutral memory-population skill/playbook/concepts;
- does not seed language/style/personality behavior instructions.

## 13. Example: smoke test after setup

```powershell
python scripts/memory_mcp_smoke.py `
  --workspace <workspace_id> `
  --db-path C:\path\to\project\.agent_memory\memory.db `
  --vector-path C:\path\to\project\.agent_memory\vectors.lance `
  --require-capabilities `
  --json
```

Expected:

```json
{
  "status": "ok",
  "result": {
    "has_memory_context": true,
    "has_agent_capabilities": true
  }
}
```

## 14. Example: audit memory health

```powershell
python scripts/memory_audit.py `
  --workspace <workspace_id> `
  --db-path .agent_memory\memory.db `
  --vector-path .agent_memory\vectors.lance `
  --json
```

Expected:

```json
{
  "status": "ok",
  "failures": [],
  "warnings": [],
  "counts": {
    "chunks": 665,
    "chunks_fts": 665,
    "vectors": 665,
    "missing_embedding_ids": 0,
    "new_candidates": 0,
    "hygiene_findings": 0
  }
}
```

Counts above are illustrative. The invariant matters more than the exact number.

## 15. Example: full trust dashboard

```powershell
python scripts/memory_trust_dashboard.py `
  --workspace <workspace_id> `
  --db-path .agent_memory\memory.db `
  --vector-path .agent_memory\vectors.lance `
  --project-root . `
  --json
```

Expected:

```json
{
  "status": "ok",
  "warnings": [],
  "failures": [],
  "components": {
    "integrity": {"status": "ok"},
    "hygiene": {"status": "ok"},
    "mcp_smoke": {"status": "ok"},
    "contract": {"status": "ok"}
  }
}
```

## 16. Example: retrieval quality test

Use two tests:

1. Exact lookup:

```json
{"workspace_id": "<workspace_id>", "query": "heap_watchdog", "mode": "fts", "limit": 10}
```

2. Semantic context:

```json
{
  "workspace_id": "<workspace_id>",
  "query": "why did the bot freeze and stop updating paper feed?",
  "max_tokens": 2500
}
```

Analyst expectation:

- exact token appears through FTS;
- paraphrase appears through vector or hybrid retrieval;
- `memory_get_context` includes enough structured context to act.

## 17. Evaluation checklist for analyst

### Functional completeness

- [ ] HTTP endpoints exist and return expected schema.
- [ ] MCP tools are registered and callable.
- [ ] `memory_get_context` returns structured sections.
- [ ] `memory_search` returns exact FTS hits.
- [ ] Ingest writes episode + chunk + FTS + vector row.
- [ ] File ingest is idempotent.
- [ ] Candidate-first extraction does not silently promote decisions.
- [ ] Decisions support supersedes.
- [ ] Theories support statuses and evidence.
- [ ] Experiments/results link to theories and snapshots.
- [ ] Roles/skills/playbooks are retrievable and linkable.
- [ ] Behavior instructions appear in context and respect conflict policy.
- [ ] Neutral setup seed does not write behavior instructions.

### Retrieval correctness

- [ ] Exact IDs appear in top 3 for FTS.
- [ ] Paraphrase queries appear in top 10 with vector enabled.
- [ ] `memory_get_context` includes expected chunk/object under token budget.
- [ ] Active decisions do not bury current theories and capabilities.
- [ ] Rejected theories remain searchable.

### Integrity

- [ ] `PRAGMA integrity_check == ok`.
- [ ] `chunks == chunks_fts`.
- [ ] `chunks == vectors`.
- [ ] `missing_embedding_ids == 0`.
- [ ] workspace pollution is zero.
- [ ] open maintenance events are zero or explicitly justified.

### Content discipline

- [ ] No stale candidates.
- [ ] No active theories without validation criteria.
- [ ] No active theories without evidence unless intentionally new.
- [ ] No overdue experiments without result/status.
- [ ] Important decisions have rationale/source.
- [ ] Important objects have capability links.

### Operations

- [ ] Repair requires explicit flags.
- [ ] Repair requires backup-first for real DB.
- [ ] `/health` is non-mutating.
- [ ] Watchdog is detect-only.
- [ ] Trust dashboard reports useful status without false project-name warnings.

## 18. Known risk areas to inspect

1. **Retrieval quality is not guaranteed by parity alone.**
   - `chunks == vectors` only proves index completeness.
   - Analyst must test sentinel queries.

2. **Content quality depends on agent discipline.**
   - Schema cannot force good theories/evidence by itself.
   - Hygiene report is the enforcement layer.

3. **Behavior instructions can become stale.**
   - Current user request and system/developer instructions must override stale memory.
   - Conflict policy must be evaluated.

4. **MCP and HTTP can point to different DBs if runtime config is stale.**
   - Always check DB path, workspace id, and MCP smoke after setup/restart.

5. **Project-specific docs should not be judged as generic docs.**
   - The contract checker must allow project names in project roots but keep generic docs clean.

## 19. Suggested analyst scenarios

### Scenario A: new project bootstrap

1. Delete test `.agent_memory`.
2. Run `setup_agent.py --project`.
3. Confirm neutral seed exists.
4. Confirm no behavior instructions were seeded.
5. Run `memory_trust_dashboard.py`.

Pass condition:

```text
trust_dashboard.status == ok
behavior_instructions_written == 0
agent_skills includes Memory population discipline
agent_playbooks includes Neutral memory bootstrap
```

### Scenario B: vector drift simulation

1. Create test DB with several chunks.
2. Delete one LanceDB vector row or run audit against incomplete vector store.
3. Run `memory_audit.py`.
4. Confirm degraded/warning status and repair hints.
5. Run explicit backup-first repair.

Pass condition:

```text
audit detects missing vector
repair requires explicit flag
post-repair audit returns ok
```

### Scenario C: candidate review

1. Ingest an episode that produces candidates.
2. Confirm candidates are `new`.
3. Promote one supported candidate.
4. Reject one weak candidate.

Pass condition:

```text
active decision/rule appears only after promote
rejected candidate remains in audit trail
```

### Scenario D: research workflow

1. Register snapshot.
2. Write theory.
3. Write experiment linked to snapshot/theory.
4. Add experiment result.
5. Confirm theory evidence/status changes.

Pass condition:

```text
research agenda is coherent
theory has evidence
hygiene has no critical finding
```

## 20. Current readiness assessment

Based on current repository structure and recent live checks:

- The core memory product is functionally broad enough for real project use.
- The critical previous gaps are now covered:
  - FTS/vector parity checks;
  - workspace isolation;
  - behavior instructions;
  - roles/skills/playbooks;
  - candidate-first promotion;
  - hygiene and watchdog;
  - neutral setup seed.
- The remaining analyst focus should be quality evaluation:
  - retrieval sentinel coverage;
  - long-running drift behavior;
  - whether agents consistently write theories/evidence instead of dumping everything into episodes;
  - usability of reports for non-developer operators.

## 21. Minimal command pack for analyst

```powershell
# From agent-memory-lite repo
python scripts/status.py
python scripts/memory_audit.py --workspace <workspace_id> --db-path <project>\.agent_memory\memory.db --vector-path <project>\.agent_memory\vectors.lance --json
python scripts/memory_hygiene.py --workspace <workspace_id> --db-path <project>\.agent_memory\memory.db --json
python scripts/memory_mcp_smoke.py --workspace <workspace_id> --db-path <project>\.agent_memory\memory.db --vector-path <project>\.agent_memory\vectors.lance --require-capabilities --json
python scripts/memory_trust_dashboard.py --workspace <workspace_id> --db-path <project>\.agent_memory\memory.db --vector-path <project>\.agent_memory\vectors.lance --project-root <project> --json
python scripts/research_status.py --workspace <workspace_id>
```

## 22. Analyst verdict template

```text
Verdict: pass | pass with warnings | fail

Retrieval:
- FTS exact:
- Vector semantic:
- get_context quality:

Integrity:
- SQLite:
- FTS parity:
- Vector parity:
- Workspace isolation:

Research discipline:
- Theories:
- Evidence:
- Experiments:
- Insights:
- Candidates:

Capability influence:
- Roles:
- Skills:
- Playbooks:
- Capability links:

Operational trust:
- Audit:
- Hygiene:
- MCP smoke:
- Dashboard:
- Backup/repair:

Main risks:
1.
2.
3.

Recommended next changes:
1.
2.
3.
```

