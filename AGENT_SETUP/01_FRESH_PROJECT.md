# Prompt: set up agent-memory-lite for THIS project from scratch

Paste everything between the lines below as the first user message in a new
chat. The agent will detect the project, locate the agent-memory-lite repo,
bootstrap a project-scoped memory if missing, configure the MCP server, and
verify end-to-end. No follow-up questions to you, except possibly "please
restart your runtime" once at the end.

---

You are an autonomous setup agent. Your single goal in this turn is to
configure agent-memory-lite for the project that is currently open. After
this turn, every future chat in this project must transparently use
project-scoped persistent memory via MCP tools.

# Step 0 — silent failure mode

You may NOT ask the user any clarifying questions. If something is
ambiguous, choose the safest default and document it in your final report.
The only acceptable user-facing message is the final report at the end.

# Step 1 — locate the project root and the agent-memory-lite repo

PROJECT_ROOT = your current working directory (or the git toplevel if you
can determine it via `git rev-parse --show-toplevel`).

REPO_ROOT = the agent-memory-lite checkout. Search in this order:
1. `C:/Users/Osino/Desktop/work/agent-memory-lite`
2. `~/agent-memory-lite`
3. `~/work/agent-memory-lite`
4. `~/Documents/agent-memory-lite`
5. The result of `git -C ~ -C C:/Users/Osino -C "$HOME" --no-pager grep -l "agent-memory-lite" 2>/dev/null` style search.

If REPO_ROOT cannot be found, stop and report:
"agent-memory-lite repo not found in standard locations. Tell me its path
and re-run." — then exit. Do not improvise a partial setup.

VENV_PYTHON = `<REPO_ROOT>/.venv/Scripts/python.exe` on Windows, or
`<REPO_ROOT>/.venv/bin/python` elsewhere. If neither exists, fall back to
`python` and note the deviation.

# Step 2 — check whether memory is already wired for THIS project

If `<PROJECT_ROOT>/.agent_memory/memory.db` exists AND
`<PROJECT_ROOT>/.claude/settings.json` contains an `mcpServers.agent-memory-lite`
entry whose `env.MEMORY_DB_PATH` points at that exact db file, the project
is already configured. Skip to Step 4 (verify).

# Step 3 — run the project-scoped setup

Execute exactly:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/setup_agent.py --project <PROJECT_ROOT>
```

Capture stdout and stderr. The script is idempotent — safe to re-run if it
fails partway. Expected effects:
- Creates `<PROJECT_ROOT>/.agent_memory/memory.db`
- Writes `<PROJECT_ROOT>/.claude/settings.json` with the MCP entry whose
  env pins MEMORY_DB_PATH and VECTOR_DB_PATH to this project
- Writes `<PROJECT_ROOT>/CLAUDE.md` and `<PROJECT_ROOT>/AGENTS.md`
  containing the agent contract
- Runs an MCP smoke test (initialize + tools/list)

If the script reports a missing prerequisite (package not installed, mcp
extra missing, Ollama unreachable), follow its `[!]` hints exactly:
- `pip install -e ".[dev,mcp]"` from REPO_ROOT if the package is missing
- `ollama pull qwen2.5:7b-instruct` if the model isn't pulled (this takes
  several minutes and ~5 GB; only do it if Ollama is reachable, otherwise
  let `OLLAMA_PROBE_SKIP=true` stand and accept that LLM extraction will
  be heuristic-only)

Do NOT install Ollama itself — that requires a GUI installer. If Ollama
isn't installed, just continue without it.

# Step 4 — verify the MCP tools are reachable in THIS turn

Try to call `memory_get_context` with this payload:

```json
{"workspace_id": "default", "query": "self-test from setup", "max_tokens": 500}
```

Three outcomes:

a) The tool returns a JSON envelope with a `context_text` field — success.
   Move to Step 5.

b) The tool is not in your tool list (you cannot call it) — Claude Code
   reads settings.json at startup, so the new MCP entry won't appear until
   the user restarts. Report this to the user as the only required manual
   step. Skip Step 5.

c) The tool exists but returns an error — capture the error text. If it
   mentions "service unreachable" or port 8765, the project mode does not
   need the HTTP service so this should not happen. If it does, report the
   error verbatim and stop.

# Step 5 — leave a setup-complete episode in the project's memory

If Step 4 succeeded, write one episode to mark the install:

```json
{
  "workspace_id": "default",
  "source_type": "system",
  "raw_text": "agent-memory-lite configured for project '<PROJECT_NAME>' on <ISO_DATE>. MCP tools verified.",
  "trust_level": "verified_by_tool",
  "importance": 0.6
}
```

# Step 6 — final report to the user

Print exactly this structure (fill in the blanks):

```
agent-memory-lite is set up for <PROJECT_NAME>.

memory db:  <absolute path to .agent_memory/memory.db>
contract:   <PROJECT_ROOT>/CLAUDE.md, <PROJECT_ROOT>/AGENTS.md
mcp config: <PROJECT_ROOT>/.claude/settings.json
ollama:     <ok | not running | not installed — LLM extraction is no-op>
mcp tools:  <verified | NOT VISIBLE — please restart Claude Code>

What this means for our future chats in this project:
- Before non-trivial work I will call memory_get_context to load prior context.
- After meaningful actions I will call memory_ingest_episode.
- After architectural choices I will call memory_write_decision.
- After task progress I will call memory_update_task_state.
All of this writes to <project>/.agent_memory/memory.db only — no
cross-project leakage.

Now tell me what you would like to do.
```

If MCP tools were not visible in Step 4, append:

```
ACTION REQUIRED: close and reopen Claude Code so it picks up the new
.claude/settings.json. After restart, the memory tools will be live.
```
