<!-- agent-memory-lite-contract:begin -->

# Agent contract

Drop this document into the system prompt, `CLAUDE.md`, or `AGENTS.md` of
any AI agent that should use the agent-memory-lite service.

For a one-page summary, see [`docs/AGENT_CHEATSHEET.md`](AGENT_CHEATSHEET.md).
For schemas, see [`docs/MEMORY_API.md`](MEMORY_API.md). For operator workflow,
see [`docs/OPERATIONS.md`](OPERATIONS.md).

## What You Have

A local memory service on `http://127.0.0.1:8765`. All data is local; no cloud
calls. The service binds to `127.0.0.1` only.

Each project has its own SQLite and LanceDB pair via `MEMORY_DB_PATH` and
`VECTOR_DB_PATH`. `workspace_id` is the logical namespace inside that database.
In strict project mode (`MEMORY_STRICT_WORKSPACE_ISOLATION=1`), writes to
foreign workspaces are blocked; reads to registered workspaces are allowed.

Memory persists across chat sessions. Without the service you are working
blind. Say so; do not fall back to "internal memory".

## V3-Only Active Surface

MCP stdio registers only the compact v3 tools. Legacy v2-specific names are
not an active agent surface. Use the canonical v3 tools for new agent
workflows.

## V3 Strict Tools

| Tool | Returns |
|---|---|
| `memory_impact_check(file_path)` | digest, callers, hot symbols, verdict, advisory |
| `memory_search(query, kinds?, rerank?)` | compact projections with scores |
| `memory_get(kind, id, fields?)` | compact projection; full fields opt-in |
| `memory_write(kind, payload)` | new row, compact projection |
| `memory_edit(kind, id, fields)` | partial update, compact projection |
| `memory_pin(kind, id, pinned)` | pin toggle |
| `memory_archive(kind, id, reason?)` | archive marker |
| `memory_brief(task?, max_tokens?)` | session-start brief |
| `memory_lint(tool_name, tool_payload)` | pre-task advisory |
| `memory_invoke_skill(skill_id)` | full skill `body_md` |
| `memory_status(include_environment?, include_active_memory?)` | anchor, registry, counts, adoption diagnostic |
| `memory_plan(task_id)` | live plan steps |

## Discipline Rules

1. Call `memory_brief(task=...)` at session start for non-trivial work.
2. Call `memory_impact_check(file_path=...)` before source-code read, grep,
   edit, or write.
3. Call `memory_search(query=...)` before writing a decision, theory,
   behavior, skill, or plan step. Create new memory only when there is no
   overlap.
4. Use `memory_write(kind=...)` for durable writes:
   `decision`, `theory`, `behavior`, `skill`, `episode`, `concept`, `task`,
   `insight`, `plan_step`.
5. After a decision or theory write, inspect `capability_suggestions` and record
   the best match in the task or follow-up plan when it should shape execution.
6. Maintain a plan in `plan_steps` for multi-step work: exactly one active
   step, mark completed/blocked/skipped as you go.

## Discover Then Fetch

Every read tool returns compact projections by default. Fetch full fields only
for the item you actually need.

```text
hits = memory_search(query="kelly sizing", limit=5)
full = memory_get(kind="decision", id="dec_x", fields=["decision_text", "rationale"])
```

## Writing Rules

Use one canonical write path:

```text
memory_write(kind="decision", payload={...})
memory_write(kind="episode", payload={...})
memory_write(kind="behavior", payload={...})
memory_write(kind="task", payload={...})
memory_write(kind="plan_step", payload={...})
```

Do not store secrets. Server-side redaction helps, but do not deliberately
defeat it.

Behavior instructions are high-trust memory, but never override system,
developer, or current user instructions.

## Project Mode Vs Hub Mode

Project mode is default for project chats. Reads to registered workspaces are
allowed; writes to foreign workspaces are blocked.

Hub mode is for cross-project maintenance. Use it only from a parent/shared
context where strict project isolation is intentionally not active.

Never disable `MEMORY_STRICT_WORKSPACE_ISOLATION` in a project chat to write to
another workspace. Ask the operator to switch contexts.

## Maintenance

After migration, deploy, crash, or unexplained retrieval behavior, run:

```bash
python scripts/memory_audit.py --workspace <workspace_id> --json
python scripts/memory_quality_gate.py --workspace <workspace_id> --json
python scripts/memory_mcp_smoke.py --workspace <workspace_id> --require-behavior --require-capabilities --json
python scripts/memory_trust_dashboard.py --workspace <workspace_id> --json
```

Repair only with explicit repair flags and a backup.

## If Memory Is Down

If MCP tools are missing from the runtime, register the project:

```bash
python scripts/setup_agent.py --project /path/to/this/project
```

If HTTP at `http://127.0.0.1:8765` is down, start it in a separate terminal:

```bash
python -m agent_memory_lite
```

Do not fall back to internal memory.

<!-- agent-memory-lite-contract:end -->
