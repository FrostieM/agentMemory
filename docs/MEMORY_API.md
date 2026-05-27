# Memory API reference

`agent-memory-lite` exposes a v3-only active agent surface. Historical v1/v2
routes may still exist in old changelog files or historical migrations, but
new agents and runtime integrations should use the endpoints below.

Base URL: `http://127.0.0.1:8765`

Every v3 endpoint returns:

```json
{"ok": true, "data": {}, "error": null}
```

or:

```json
{"ok": false, "data": null, "error": {"code": "not_found", "message": "..."}}
```

## Agent Surface

| Method | Path | Body / params |
|---|---|---|
| GET | `/memory/brief` | `workspace_id, task?, max_tokens?, session_id?` |
| GET | `/memory/impact_check` | `workspace_id, file_path, callers_limit?, hot_threshold?` |
| POST | `/memory/search` | `{workspace_id, query, kinds?, limit?, rerank?}` |
| GET | `/memory/get` | `workspace_id, kind, id, fields?` |
| POST | `/memory/write` | `{workspace_id, kind, payload, agent_id?, source_episode_id?}` |
| POST | `/memory/edit` | `{workspace_id, kind, id, fields, agent_id?}` |
| POST | `/memory/pin` | `{workspace_id, kind, id, pinned?, agent_id?}` |
| POST | `/memory/archive` | `{workspace_id, kind, id, reason?, agent_id?}` |
| POST | `/memory/lint` | `{workspace_id, tool_name, tool_payload, transcript_path?}` |
| GET | `/memory/skill/{skill_id}` | `workspace_id` |
| GET | `/memory/status` | `workspace_id, include_environment?, include_active_memory?` |
| GET | `/memory/plan` | `workspace_id, task_id` |

MCP stdio registers the same 12 tool names:

```text
memory_brief
memory_impact_check
memory_search
memory_get
memory_write
memory_edit
memory_pin
memory_archive
memory_lint
memory_invoke_skill
memory_status
memory_plan
```

Legacy v2-specific MCP names are not registered.

## Kinds

Generic v3 reads and writes accept these kinds:

```text
decision
theory
behavior
skill
episode
concept
task
insight
code_digest
chunk
plan_step
```

## Discover Then Fetch

Search returns compact projections. Fetch full fields only when a specific hit
is worth the token cost.

```bash
curl -s -X POST http://127.0.0.1:8765/memory/search \
  -H "Content-Type: application/json" \
  -d '{"workspace_id":"agent-memory-lite","query":"v3 roadmap","limit":5}'
```

```bash
curl -s "http://127.0.0.1:8765/memory/get?workspace_id=agent-memory-lite&kind=decision&id=dec_x&fields=decision_text,rationale"
```

## Plan Steps

Use `memory_write(kind="plan_step")` to persist multi-step project plans.
The writer assigns `rank` automatically.

```json
{
  "workspace_id": "agent-memory-lite",
  "kind": "plan_step",
  "payload": {
    "task_id": "project-10-10-roadmap",
    "title": "Remove active legacy MCP surface",
    "status": "done"
  }
}
```

Read the plan back with:

```text
GET /memory/plan?workspace_id=agent-memory-lite&task_id=project-10-10-roadmap
```

## Operational Endpoints

The service also keeps read-only or operator-facing HTTP endpoints for the UI,
workspaces, health checks, review queues, code graph pages, and maintenance
scripts. They are not the agent hot path. New agent integrations should start
from the 12-tool surface above.

Important operator checks:

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | service, DB, vector, and migration health |
| GET | `/memory/workspaces` | registered workspaces |
| GET | `/ui` | Observatory |
| GET | `/ui/metrics` | brain metrics |
| GET | `/ui/review` | candidate review queue |

## Removed From Active Surface

Legacy v2-specific names are intentionally absent from MCP stdio and should
not appear in new agent workflows.

Use `memory_write`, `memory_edit`, `memory_search`, `memory_get`, and
`memory_plan` instead.
