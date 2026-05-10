# Memory API reference

Full endpoint and MCP tool schemas for `agent-memory-lite`. The agent operating
contract lives in [`AGENT_CONTRACT.md`](AGENT_CONTRACT.md) — read that first;
this file is the lookup table for "what's the JSON shape of endpoint X".

All endpoints accept JSON and return JSON. Default header:
`Content-Type: application/json`. Service base URL: `http://127.0.0.1:8765`.

## Read endpoints

### POST /memory/get_context (primary surface)

```json
{
  "workspace_id": "<workspace_id>",
  "task_id": "<optional>",
  "query": "<freeform RU/EN>",
  "files_in_scope": ["src/foo/bar.py"],
  "max_tokens": 3500,
  "historical": false
}
```

Response: `{"context_text": "<memory_context>...</memory_context>", "sources": [...]}`.
Feed `context_text` to your LLM verbatim.

The envelope contains, in priority order:

- `<core_memory>` — durable project constraints.
- `<behavior_instructions>` — communication style, operating behavior, project
  conventions, conflict policies.
- `<task_state>` — current goal/status/next_action for `task_id`.
- `<active_decisions>` — supersedes-aware architectural choices.
- `<active_theories>` — working hypotheses, mechanisms, predictions, evidence.
- `<research_agenda>` — snapshots, open experiments, insights, concepts.
- `<agent_capabilities>` — relevant roles, skills, playbooks.
- `<procedural_rules>` — operating rules for the agent.
- `<retrieved_facts>` — temporal graph hits.
- `<retrieved_chunks>` — FTS + vector hits via reciprocal rank fusion.
- `<pending_review>` (when populated) — pending `decision_candidates` /
  `insight_candidates` / `correction_candidates` rows the operator has not yet
  promoted or rejected. Each `<ref>` carries `id`, `kind`, `title`, and (for
  decision candidates) the source `theory_id`. When the count attribute exceeds
  the per-kind cap of 5, call `/memory/list_candidates` for the long tail.

`<active_decisions>` is query-ranked and capped so durable decisions remain
useful without burying current theories. The highest-ranked decisions in
context render with full text so critical endings aren't silently clipped.
`<retrieved_chunks>` suppresses stale low-score noise in normal mode while
preserving exact top FTS hits; use `historical=true` when you intentionally
need older chunks.

**Discover-then-fetch pattern.** Each structured section (decisions, theories,
behavior_instructions, agent_capabilities) renders top-N items in full; the
rest of the relevant matches appear inside an `<index>` block as compact
`<ref id="..." title="..."/>` entries. The agent should:

1. Read the full items as primary signal.
2. Scan the `<index>` block; if a ref looks important, call
   `memory_get_object(kind, id)` to expand. Do NOT fuzzy-search by title.
3. The `<index>` block reports `total / full / listed / hidden` counts.

### POST /memory/get_object

```json
{
  "workspace_id": "<workspace_id>",
  "kind": "decision|theory|snapshot|experiment|insight|concept|role|skill|playbook|behavior_instruction",
  "id": "dec_...",
  "include_evidence": false
}
```

Returns the full body of a single memory object. Use after seeing a `<ref/>`
in `memory_get_context`. Pass `include_evidence=true` for theories.

### POST /memory/explain_context

```json
{
  "workspace_id": "<workspace_id>",
  "query": "<same query passed to get_context>",
  "max_tokens": 3500,
  "historical": false
}
```

Read-only audit of why `memory_get_context` returned what it did. Returns
source candidates, merged scores, included ids, section counts, and whether
each scored chunk was included.

### POST /memory/search

```json
{"workspace_id": "<workspace_id>", "query": "<token>", "mode": "fts", "limit": 10}
```

Exact symbol/path/error-string lookup. Results are BM25 ordered.

### POST /memory/list_decisions

```json
{
  "workspace_id": "<workspace_id>",
  "query": "live execution",
  "include_superseded": false,
  "limit": 10
}
```

Set `include_superseded=true` for architecture archaeology before changing a
design.

### POST /memory/list_theories

```json
{
  "workspace_id": "<workspace_id>",
  "query": "source-flip tennis favorite",
  "include_evidence": true,
  "statuses": ["testing", "validated", "rejected"],
  "limit": 10
}
```

### POST /memory/list_research_agenda

```json
{
  "workspace_id": "<workspace_id>",
  "query": "paper selector open-rate",
  "limit": 10
}
```

### POST /memory/list_agent_capabilities

```json
{
  "workspace_id": "<workspace_id>",
  "query": "live flow health audit",
  "limit": 6
}
```

### POST /memory/list_behavior_instructions

```json
{
  "workspace_id": "<workspace_id>",
  "query": "incident report communication style",
  "kinds": ["communication_style"],
  "limit": 10
}
```

### POST /memory/list_capability_links

```json
{
  "workspace_id": "<workspace_id>",
  "target_type": "theory",
  "target_id": "th_...",
  "limit": 50
}
```

### POST /memory/list_candidates

```json
{
  "workspace_id": "<workspace_id>",
  "statuses": ["new"],
  "limit": 20
}
```

### POST /memory/list_audit

```json
{
  "workspace_id": "<workspace_id>",
  "target_type": "decision",
  "target_id": "dec_...",
  "since": "2026-04-01T00:00:00Z",
  "until": "2026-05-01T00:00:00Z",
  "action": "write_decision",
  "limit": 50
}
```

### POST /memory/list_maintenance_events

```json
{
  "workspace_id": "<workspace_id>",
  "statuses": ["open"],
  "limit": 20
}
```

### POST /memory/what_references

```json
{"workspace_id": "<workspace_id>", "target_id": "dec_...", "limit": 50}
```

Returns every memory row mentioning `target_id` across nine tables in one
call. Replaces fanning out across per-kind list endpoints and filtering.

### POST /memory/review_queue

```json
{"workspace_id": "<workspace_id>", "limit_per_kind": 10}
```

Short list of memory rows needing operator action. Each item carries the
suggested `action` (`promote_candidate`, `resolve_maintenance_event`).
Distinct from `/memory/hygiene_report` (broader scan) and
`/memory/quality_gate` (research-trust gate).

### POST /memory/snapshot_list

```json
{"workspace_id": "<workspace_id>", "limit": 20}
```

### POST /memory/snapshot_diff

```json
{"workspace_id": "<workspace_id>", "before_id": "memst_a", "after_id": "memst_b"}
```

Returns counts deltas plus `added` / `removed` / `changed` id sets.

### POST /memory/compact_trigger

```json
{"workspace_id": "<workspace_id>"}
```

Probes whether the workspace's chunk count + stale ratio is past
`MEMORY_COMPACT_TRIGGER_THRESHOLD_CHUNKS`. Emits a `compaction_due`
maintenance event when overdue. The probe never runs compaction itself.

### Date-range filters on listings

`/memory/list_decisions`, `/memory/list_theories`, `/memory/list_candidates`,
`/memory/list_behavior_instructions`, and `/memory/list_research_agenda` accept
optional ISO-8601 `since` / `until` endpoints (both inclusive, either side
optional).

### GET /memory/hygiene_report

```text
GET /memory/hygiene_report?workspace_id=<workspace_id>
```

Specific content-discipline findings: stale candidates, theories without
validation/evidence, overdue or stale experiments, unlinked insights, important
decisions without provenance, important objects without role/skill/playbook
influence. Missing-link findings include `suggested_capability_links` payloads
ready to pass to `memory_link_capability`.

### GET /memory/quality_gate

```text
GET /memory/quality_gate?workspace_id=<workspace_id>
```

Strict content-quality findings for research-grade trust. Reports `degraded`
when important theories aren't testable, terminal theories lack evidence,
important experiments lack success criteria, or important decisions lack
provenance.

### GET /memory/workspaces

Lists registered workspaces with `id`, `label`, `db_path`, `vector_path`,
`project_root`, `is_current`.

### GET /health

Operational. Reports `version`, `applied_migrations`, `retrieval_integrity`
(`status`, `failures`, `warnings`, counts, repair hints).

## Write endpoints

### POST /memory/ingest_episode

```json
{
  "workspace_id": "<workspace_id>",
  "session_id": "<chat-id, optional>",
  "task_id": "<task-id, optional>",
  "source_type": "agent_action",
  "raw_text": "<plain text describing what happened>",
  "trust_level": "agent_observed",
  "importance": 0.6
}
```

Response includes `candidates_written`. Candidates aren't active until reviewed.

### POST /memory/ingest_file

```json
{"workspace_id": "<workspace_id>", "path": "<relative path>", "content": "<file text>", "language": "python"}
```

Idempotent: if `content_hash` matches the prior version, returns
`skipped: true` with no new chunks.

### POST /memory/record_with_evidence (compound — Move 2 of v2.2)

```json
{
  "workspace_id": "<workspace_id>",
  "evidence_text": "<the observation that supports this decision>",
  "evidence_trust_level": "agent_observed",
  "evidence_importance": 0.6,
  "decision_title": "<short title>",
  "decision_text": "<one-paragraph statement>",
  "decision_rationale": "<why this and not alternative>",
  "decision_importance": 0.8,
  "decision_confidence": 0.9,
  "supersedes_decision_id": "dec_...",
  "capability_type": "skill",
  "capability_name": "<existing skill name>",
  "capability_relation": "method"
}
```

Bundles `ingest_episode` + `write_decision` + optional `link_capability`
into one atomic call so the agent doesn't have to remember each
discipline-rule follow-up. The decision's `source_episode_id` is wired
to the just-created episode automatically; the capability link's
`source_episode_id` likewise. The capability triplet is all-or-nothing
— provide all three or none.

Response:

```json
{
  "workspace_id": "<workspace_id>",
  "episode_id": "ep_...",
  "decision_id": "dec_...",
  "decision_status": "active",
  "valid_from": "2026-...",
  "superseded_decision_id": null,
  "capability_link_id": "caplink_..." | null,
  "chunk_id": "chk_...",
  "capability_suggestions": [
    {"capability_type": "skill", "capability_id": "sk_...",
     "capability_name": "<top-ranked match>", "score": 0.42,
     "snippet": "<first 80 chars of summary>"}
  ]
}
```

`capability_suggestions` (Move 3) is populated only on the no-link
path — i.e. when the caller didn't pass the capability triplet. Each
suggestion is a top-N candidate ranked by token-overlap coefficient
between the decision text and the capability's name + summary. Empty
list when a link was already created or no capability matched.

The same operation is exposed as the MCP tool
`memory_record_with_evidence`.

### POST /memory/write_decision

```json
{
  "workspace_id": "<workspace_id>",
  "title": "<short title>",
  "decision_text": "<one-paragraph statement>",
  "rationale": "<why this and not alternative>",
  "supersedes_decision_id": "dec_...",
  "source_episode_id": "ep_...",
  "allow_orphan": false
}
```

When `source_episode_id` is omitted and `allow_orphan` is `false`, the
server (with `MEMORY_AUTOTHREAD_DECISION_SOURCE=true`, default ON)
auto-fills it from the agent's most recent `memory_ingest_episode` in
the same workspace within a 10-minute window — Move 1 of v2.2. The
`X-Memory-Agent-Id` header determines which agent's history is
consulted; without the header an anonymous 60-second fallback
applies. Pass `allow_orphan: true` to deliberately write an untraced
decision (e.g. the choice predates the recording of any episode).

Response:

```json
{
  "decision_id": "dec_...",
  "status": "active",
  "valid_from": "2026-...",
  "superseded_decision_id": null,
  "source_episode_id": "ep_..." | null,
  "capability_suggestions": [
    {"capability_type": "skill", "capability_id": "sk_...",
     "capability_name": "<top-ranked match>", "score": 0.42,
     "snippet": "<first 80 chars of summary>"}
  ]
}
```

`capability_suggestions` (Move 3) lists the top-3 workspace
capabilities ranked by token-overlap with title + decision_text +
rationale. Read-only hint — the agent decides whether to call
`memory_link_capability`. Empty when no capability matches.

### POST /memory/write_theory

```json
{
  "workspace_id": "<workspace_id>",
  "title": "Source-flip tennis favorites",
  "domain": "trading.paper.edge",
  "claim": "Source-flip trades on tennis favorites may carry short-lived edge.",
  "mechanism": "The source wallet may react before public odds fully adjust.",
  "predictions": ["favorite-side flips outperform underdog-side flips"],
  "validation_criteria": [
    "minimum 100 settled trades",
    "net edge remains positive after fee assumptions"
  ],
  "experiment_plan": "Replay source-flip fills by sport and side.",
  "dependent_decision_ids": ["dec_..."],
  "tags": ["trading-bot", "source-flip", "tennis", "favorite"],
  "status": "testing",
  "confidence": 0.35,
  "importance": 0.9,
  "source_episode_id": "ep_...",
  "allow_orphan": false
}
```

Theory status values: `proposed`, `testing`, `supported`, `validated`,
`weakened`, `rejected`, `superseded`, `archived`. Prefer `validated` only when
validation criteria are satisfied. Prefer `rejected` for anti-theories.

`source_episode_id` and `allow_orphan` follow the same Move 1
semantics as `write_decision`: omit `source_episode_id` to let the
server auto-thread it from your most recent `memory_ingest_episode`,
or pass `allow_orphan: true` for a deliberate untraced theory.

Response includes `capability_suggestions` (Move 4) — top-3
workspace capabilities ranked by token-overlap with title + claim +
mechanism. Same shape and same read-only contract as on the
decision side. Available on every transport (HTTP route, MCP stdio
local fallback, in-process MCP) so MCP-only deployments see the
hint identically to HTTP callers.

### POST /memory/add_theory_evidence

```json
{
  "workspace_id": "<workspace_id>",
  "theory_id": "th_...",
  "kind": "supporting",
  "summary": "<what the data showed>",
  "artifact_path": "reports/analitic/replay.md",
  "metrics": {"n": 42, "roi": 0.031},
  "confidence": 0.8
}
```

### POST /memory/register_snapshot

```json
{
  "workspace_id": "<workspace_id>",
  "snapshot_key": "server_20260427T105823",
  "title": "VPS database snapshot before reset",
  "source": "vps",
  "db_path": "research/db_snapshots/server_bot_20260427T105823Z.db",
  "duckdb_path": "research/snapshots/server_20260427T105823/research.duckdb",
  "table_counts": {"trade_decision_fact": 49452, "bot_paper_positions": 191},
  "total_rows": 499141
}
```

### POST /memory/write_experiment

```json
{
  "workspace_id": "<workspace_id>",
  "theory_id": "th_...",
  "snapshot_id": "snap_...",
  "title": "Replay source-flip tennis favorites",
  "hypothesis": "Favorite-side source flips outperform underdog-side flips.",
  "cohort_definition": "source-flip trades where sport=tennis and side=favorite",
  "success_criteria": {"min_trades": 100, "net_edge_bps_gt": 0},
  "command": "python scripts/replay_source_flip.py --snapshot ...",
  "priority": 0.9
}
```

### POST /memory/add_experiment_result

```json
{
  "workspace_id": "<workspace_id>",
  "experiment_id": "exp_...",
  "kind": "supporting",
  "summary": "<what the experiment showed>",
  "metrics": {"n": 144, "net_edge_bps": 31.2},
  "artifact_path": "reports/analitic/source_flip_replay.md",
  "confidence": 0.8
}
```

When linked to a theory, adjusts theory confidence/status, writes theory
evidence, and records contradiction insights for high-confidence refutations.

### POST /memory/upsert_concept

```json
{
  "workspace_id": "<workspace_id>",
  "name": "selector-gate",
  "kind": "gate",
  "definition": "Admission rule that prevents a candidate from reaching paper.",
  "aliases": ["admission gate"],
  "tags": ["trading-bot", "selector"]
}
```

### POST /memory/distill_insight

```json
{
  "workspace_id": "<workspace_id>",
  "insight_type": "open_question",
  "summary": "Sparse paper opens make overnight waits low-information unless gates are relaxed.",
  "proposed_action": "Run a soft-gate replay before another live wait.",
  "target_type": "theory",
  "target_id": "th_...",
  "confidence": 0.75
}
```

### POST /memory/update_insight

```json
{
  "workspace_id": "<workspace_id>",
  "insight_id": "insight_...",
  "target_type": "theory",
  "target_id": "th_...",
  "status": "accepted"
}
```

Use to attach an insight to a target after the fact. Do not edit SQLite
directly.

### POST /memory/upsert_agent_role

```json
{
  "workspace_id": "<workspace_id>",
  "name": "Runtime operator",
  "purpose": "Validate live system health before recovery.",
  "responsibilities": ["Check health endpoints", "Preserve evidence"],
  "boundaries": ["Do not reset data without explicit approval"],
  "handoff_triggers": ["A deeper research question appears"],
  "tools": ["/memory/get_context", "/health"],
  "confidence": 0.85
}
```

### POST /memory/upsert_agent_skill

```json
{
  "workspace_id": "<workspace_id>",
  "name": "Live flow audit",
  "summary": "Validate runtime readiness, pipeline health, and business-flow blockers.",
  "when_to_use": ["The user asks whether a live system works"],
  "inputs": ["Health JSON", "Pipeline JSON", "Recent logs"],
  "outputs": ["Exact blocker evidence", "Remaining risk"],
  "tools": ["/memory/get_context", "/memory/search"],
  "related_roles": ["Runtime operator"],
  "confidence": 0.9
}
```

### POST /memory/upsert_agent_playbook

```json
{
  "workspace_id": "<workspace_id>",
  "name": "Non-destructive live audit",
  "goal": "Confirm live flow without changing data.",
  "triggers": ["The user asks for a health check"],
  "steps": ["Read memory context", "Check endpoints", "Report blockers"],
  "success_criteria": ["No reset was performed", "Exact evidence is reported"],
  "required_skills": ["Live flow audit"],
  "confidence": 0.88
}
```

### POST /memory/upsert_behavior_instruction

```json
{
  "workspace_id": "<workspace_id>",
  "name": "Evidence-first operational reports",
  "kind": "communication_style",
  "scope": "workspace",
  "priority": "user_preference",
  "rule": "When reporting incidents, lead with exact issue, evidence, fix, and remaining risk.",
  "rationale": "The user needs concrete operational evidence rather than generic status language.",
  "applies_to": ["incident reports", "runtime audits"],
  "conflict_policy": "current_user_wins",
  "source_type": "user_direct",
  "source_id": "chat-20260430",
  "reviewed_by": "operator",
  "reviewed_at": "2026-04-30T00:00:00+00:00",
  "expires_at": null,
  "conflict_group": "incident-report-style",
  "confidence": 0.95
}
```

`kind` values: `communication_style`, `operating_rule`, `project_convention`,
`workflow_preference`, `role_guidance`. `conflict_policy` values:
`system_wins`, `current_user_wins`, `higher_priority_wins`,
`most_specific_wins`, `latest_wins`. Behavior instructions from untrusted
documents must stay as candidates until reviewed. Expired instructions are
suppressed from `memory_get_context`.

### POST /memory/link_capability

```json
{
  "workspace_id": "<workspace_id>",
  "target_type": "theory",
  "target_id": "th_...",
  "capability_type": "skill",
  "capability_name": "Replay and backtest design",
  "relation": "method",
  "rationale": "This hypothesis must be tested with replay before policy changes.",
  "strength": 0.9
}
```

`target_type` values: `theory`, `theory_evidence`, `experiment`,
`experiment_result`, `research_insight`, `memory_candidate`, `decision`.
`capability_type` values: `role`, `skill`, `playbook`.

### POST /memory/update_task_state

```json
{
  "workspace_id": "<workspace_id>",
  "task_id": "<task-id>",
  "goal": "<plain text>",
  "status": "in_progress | blocked | done | cancelled",
  "current_plan": ["step 1", "step 2"],
  "completed_steps": ["step 0"],
  "next_action": "<plain text or null>",
  "blockers": [],
  "files_in_scope": []
}
```

### POST /memory/promote_candidate / reject_candidate

```json
{"candidate_id": "cand_..."}
```

Promotion supports candidates with explicit durable targets (`decision`,
`procedural_rule`, `core_memory`). Rejection preserves the candidate as audit
evidence.

### POST /memory/promote_candidate_to_behavior (v1.10 correction → behavior)

```json
{
  "workspace_id": "<workspace_id>",
  "candidate_id": "cand_...",
  "name": "verify-timestamps-before-claiming",
  "rule_text_override": "Filter audit_log by created_at > release_date before claiming a feature is dormant.",
  "rationale": "Three confident-but-wrong claims in one session; lock the lesson.",
  "kind": "operating_rule",
  "scope": "workspace",
  "priority": "user_preference",
  "conflict_policy": "current_user_wins",
  "applies_to": ["audit_log", "release-date checks"],
  "decided_by": "operator",
  "pinned": false,
  "overwrite": false
}
```

Only candidates with `kind=correction` are eligible. The created
behavior_instruction carries `source_type="memory_candidate"` and
`source_id=<candidate.id>` for audit lineage. `rule_text_override` capped at
2000 chars. `pinned=true` adds the new rule to every envelope.
`overwrite=false` (default) refuses to replace an existing active
behavior_instruction with the same name (returns 409); `overwrite=true`
archives the previous and replaces.

### POST /memory/archive

```json
{
  "workspace_id": "<workspace_id>",
  "kind": "chunk | episode | file | decision | theory | insight | role | skill | playbook | behavior_instruction | candidate",
  "id": "<canonical id>",
  "archive": true
}
```

Universal soft-delete. Archived items disappear from `memory_get_context`
(default `historical=false`) but `memory_search` keeps returning them tagged
`is_archived: true`.

### POST /memory/pin

```json
{"workspace_id": "<workspace_id>", "kind": "decision", "id": "dec_...", "pinned": true}
```

Supported kinds: `decision`, `behavior_instruction`, `core_memory`. Pinned
items always ride the active envelope regardless of query relevance.

### POST /memory/snapshot_save

```json
{"workspace_id": "<workspace_id>", "name": "before-deploy", "metadata": {}}
```

Captures a point-in-time digest of workspace memory. NOT external research
dataset snapshots — that is `/memory/register_snapshot`.

### POST /memory/resolve_maintenance_event

```json
{"event_id": "me_...", "status": "resolved"}
```

Use `status="ignored"` only when the event is reviewed and intentionally left
unfixed.

### POST /memory/record_usage_feedback

Adjust future retrieval ranking based on whether a returned chunk, decision,
theory, insight, or capability was useful or noisy. Chunk feedback is bounded
and never overrides FTS/vector evidence.

### POST /memory/compact, POST /memory/run_evals

Operational endpoints for compaction and eval runs.

### Workspace registry write API

```text
POST /memory/workspaces  {workspace_id, db_path, vector_path, label}
DELETE /memory/workspaces/{workspace_id}
```

## Code-memory tools (v1.4 → v2.1.x)

| Tool | Purpose |
|---|---|
| `memory_find_symbols` | Find functions/classes by name or qualified-name prefix. |
| `memory_graph_neighbors` | "Who depends on X" (upstream) or "what does X depend on" (downstream). |
| `memory_breaking_changes` | Signature changes in a time window. |
| `memory_symbol_history` | Versions of a symbol over time. |
| `memory_file_digest` | Symbols, edges, narrative, recent versions for a file. |
| `memory_code_overview` | Workspace-wide file/symbol/edge counts + top-called list. |
| `memory_code_graph` | BFS through the call graph for D3-style visualization. |
| `memory_soft_neighbors` | MinHash similar_signature, co_changed neighbors. |
| `memory_claim_edit` | Advertise to other agents that you're editing a target. |
| `memory_release_edit` | Release a previously-claimed target. |
| `memory_list_active_edits` | List current claims by all agents. |

All accept `workspace_id` + tool-specific params, return JSON. Direct HTTP
equivalents under `/memory/find_symbols`, `/memory/graph_neighbors`, etc.

## Auth (when enabled)

If `MEMORY_REQUIRE_API_TOKEN=true` is set, pass the local bearer token for
`/memory/*` endpoints (`/health` stays unauthenticated):

```bash
curl -s -X POST http://127.0.0.1:8765/memory/get_context \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $(cat .agent_memory/token)" \
  -d '{"workspace_id":"<workspace_id>","query":"...","max_tokens":2500}'
```

If `MEMORY_AUDIT_API_AUTH_FAILURES=true`, rejected requests are recorded as
`api_auth_failure` maintenance events without storing the supplied token.

## Hook dedupe

The `UserPromptSubmit` hook deduplicates identical prompt/context injections
for a short TTL. Set `AGENT_MEMORY_HOOK_DEDUPE_TTL=0` only for hook debugging;
normal agents leave dedupe enabled.
