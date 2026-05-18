# V3 Agent Runtimes

How to wire each supported agent against the v3 memory surface.

The v3 architecture exposes three first-class surfaces:

1. **HTTP** at `http://127.0.0.1:8765/memory/*` — language-agnostic.
2. **MCP stdio** with `memory_*` tool names — Claude Code, Cursor, Continue.
3. **`memory-cli`** shell entrypoint — Aider, Codex CLI, CI scripts.

All three return the same `{ok, data, error}` envelope.

## Compatibility matrix

| Agent | Brief delivery | Tool surface | Lint enforcement |
|---|---|---|---|
| **Claude Code** | `UserPromptSubmit` hook → `inject_memory_brief_v3.py` | 9 MCP tools (`memory_*`) | `PreToolUse` hook → `memory_lint` via MCP |
| **Cursor / Continue** | MCP server returns brief on session init | 9 MCP tools | Advisory only (no `PreToolUse`) |
| **Codex CLI** | `memory-cli brief` at boot | HTTP + `memory-cli` | Pre-edit shell wrapper calls `memory-cli lint` |
| **Aider** | `memory-cli brief` at boot | HTTP + `memory-cli` | Pre-edit shell wrapper calls `memory-cli lint` |
| **CI / batch scripts** | `memory-cli brief --text` piped into context | HTTP + `memory-cli` | `memory-cli lint --tool-name=Edit ...` |

## Claude Code (canonical)

### 1. Register the workspace

```bash
python scripts/setup_agent.py --project /path/to/project
```

Writes per-project `MEMORY_DB_PATH` + workspace registry entry.

### 2. Enable the v3 brief hook in `~/.claude/settings.json`

```jsonc
{
  "hooks": {
    "UserPromptSubmit": [{
      "hooks": [{
        "type": "command",
        "command": "<repo>/.venv/bin/python <repo>/scripts/inject_memory_brief.py"
      }]
    }],
    "PostToolUse": [{
      "matcher": "Edit|Write|NotebookEdit",
      "hooks": [{
        "type": "command",
        "command": "<repo>/.venv/bin/python <repo>/scripts/post_edit_enqueue.py"
      }]
    }]
  }
}
```

* `UserPromptSubmit` injects a ≤500-token brief composed from compact projections.
* `PostToolUse` enqueues a digest refresh after every file edit.

### 3. MCP server registration

```bash
agent-memory-lite-mcp
```

Surface includes both v2 tool names (during transition) and the 9 `memory_*` tools.

## Cursor / Continue

Cursor's MCP support follows the same stdio protocol as Claude Code. Register
the binary as an MCP server in Cursor settings:

```json
{
  "mcpServers": {
    "agent-memory-lite": {
      "command": "/path/to/venv/bin/agent-memory-lite-mcp",
      "env": {
        "MEMORY_WORKSPACE_ID": "<workspace>",
        "MEMORY_DB_PATH": "/path/to/.agent_memory/memory.db",
        "VECTOR_DB_PATH": "/path/to/.agent_memory/vectors.lance"
      }
    }
  }
}
```

Cursor doesn't expose a `PreToolUse` hook; lint stays advisory. To compensate,
the brief delivered at session start includes the highest-priority operating
rules so the agent sees them as ambient context.

## Codex CLI / Aider (HTTP-only)

These agents don't speak MCP. Wire via shell:

### Boot — fetch the brief

```bash
# In your shell rcfile or wrapper script:
export AGENT_MEMORY_WORKSPACE=$(basename "$PWD")
export AGENT_MEMORY_BASE=http://127.0.0.1:8765

BRIEF=$(memory-cli brief --text)
echo "[memory brief]" > /tmp/memory_context.md
echo "$BRIEF" >> /tmp/memory_context.md
codex --context-file /tmp/memory_context.md ...
```

### Pre-edit lint (optional)

```bash
memory-cli lint --tool-name=Edit --payload "{\"file_path\":\"$1\"}" \
  | jq -r '.data.verdict'
```

Exit non-zero on `block`. Use as a pre-commit hook or in your `:!`-style
shell-out before running Aider's edit command.

### Post-edit digest enqueue

```bash
# Wrap your editor so every save enqueues a digest:
function aider_save_wrap() {
  aider "$@"
  for f in $(git diff --name-only); do
    echo "{\"workspace_id\":\"$AGENT_MEMORY_WORKSPACE\",\"db_path\":\"$MEMORY_DB_PATH\",\"file_path\":\"$(pwd)/$f\",\"ts\":$(date +%s)}" \
      >> ~/.agent_memory/digest_queue.jsonl
  done
}
```

## CI / batch

```bash
# Print pinned operating rules as a markdown block for a CI assistant prompt:
memory-cli list --kind=behavior --pinned-only --limit=10 \
  | jq -r '.data[] | "- " + .rule_one_line'

# Validate a planned change before merging:
memory-cli lint --tool-name=Edit \
  --payload "$(jq -n --arg f "$FILE" '{file_path: $f}')" \
  | tee lint_result.json \
  | jq -er '.data.verdict == "allow"'
```

## Brief composition

The brief returned by `memory_brief` (or `GET /memory/brief`) is composed
from 5 compact sections summing to ≤500 tokens:

| Section | Budget | Source |
|---|---:|---|
| `identity` | 100 | Workspace name + invariants + project brief |
| `behaviors` | 120 | Pinned behaviors, `rule_one_line` each |
| `decisions` | 130 | Top-5 active decisions by recency / pinned |
| `state` | 60 | Active task: `goal_one_line` + `next_action` |
| `code_hubs` | 90 | Top-10 code_digests by pagerank, `purpose_short` each |

Cached on workspace fingerprint (hash of pinned-file SHAs + active-task
`updated_at`). Cache hit ~5ms, miss ~80ms (pure SQL, no LLM).

## Tool name reference

### 6 strict MCP tools (the agent's main surface)

* `memory_search(query, kinds?, limit?, rerank?)` → list of compact projections
* `memory_get(kind, id, fields?)` → compact projection or full content opt-in
* `memory_write(kind, payload)` → compact projection of new row
* `memory_edit(kind, id, fields)` → compact projection after partial update
* `memory_pin(kind, id, pinned?)` → toggle pin bit
* `memory_archive(kind, id, reason?)` → mark archived

### 2 hook primitives

* `memory_brief(task?, max_tokens?)` → `{body_md, token_count, sections}`
* `memory_lint(tool_name, tool_payload, transcript_path?)` →
  `{verdict, applicable_rules, related_decisions, prior_failures, watch_outs}`

### 1 skill body fetch

* `memory_invoke_skill(skill_id)` → `{id, name, body_md}` — the ONLY surface
  that returns full markdown body

### HTTP-only (ops surface)

* `GET /memory/list` (paginated by kind)
* `GET /memory/count`
* `GET /memory/versions`
* `POST /memory/rollback`

Reach via `memory-cli list / count / versions / rollback` for shell access.

## Optional cross-encoder reranker

Install the extra:

```bash
pip install 'agent-memory-lite[rerank]'
```

Then request reranking per call:

```bash
curl -s -X POST http://127.0.0.1:8765/memory/search \
  -H "Content-Type: application/json" \
  -d '{"workspace_id":"ws","query":"kelly sizing","rerank":true}'
```

Failure-soft: if the extra isn't installed or the model fails to load, the
service falls back to BM25/RRF order silently. `memory-cli` defaults to
`rerank=false` to avoid cold-start cost; the HTTP service keeps the model
warm.
