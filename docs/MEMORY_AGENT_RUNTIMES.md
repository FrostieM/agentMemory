# V3 Agent Runtimes

How to wire agents against the active v3 memory surface.

## Surfaces

1. HTTP at `http://127.0.0.1:8765/memory/*`.
2. MCP stdio with the 12 compact v3 tools.
3. `memory-cli` for shell workflows.

MCP stdio no longer registers legacy v2 tool names.

## MCP Tools

The stdio server advertises:

`memory_search`, `memory_get`, `memory_write`, `memory_edit`, `memory_pin`,
`memory_archive`, `memory_brief`, `memory_lint`, `memory_invoke_skill`,
`memory_impact_check`, `memory_status`, `memory_plan`.

## Claude Code

Register the workspace:

```bash
python scripts/setup_agent.py --project /path/to/project
```

Run the MCP server:

```bash
agent-memory-lite-mcp
```

The `UserPromptSubmit` hook should call the compact brief injector, and
`PostToolUse` should enqueue file digest refreshes after edits.

## Cursor / Continue

Register the same `agent-memory-lite-mcp` command in the editor's MCP settings
with `MEMORY_WORKSPACE_ID`, `MEMORY_DB_PATH`, and `VECTOR_DB_PATH`.

## Codex CLI / Aider

Use HTTP or `memory-cli`:

```bash
memory-cli brief --workspace <id>
memory-cli search --workspace <id> --query "topic"
```

## Guardrail

If an integration still needs old v2-specific calls, it is on the legacy
backlog and should be ported to `memory_brief`, `memory_search`, `memory_get`,
and `memory_write`.
