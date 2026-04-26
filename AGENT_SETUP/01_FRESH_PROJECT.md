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

REPO_ROOT = the agent-memory-lite checkout. Try these in order, stop at
the first hit:

1. The `AGENT_MEMORY_LITE_HOME` environment variable, if set.
2. Whatever Python reports as the install location of the package:
   ```
   python -c "import agent_memory_lite, pathlib; print(pathlib.Path(agent_memory_lite.__file__).resolve().parents[2])"
   ```
   (The package layout is `<repo>/src/agent_memory_lite/__init__.py`, so
   `parents[2]` is the repo root. This works only if the venv with
   `pip install -e .` is on PATH or you can find one.)
3. Common workspace folders the user is likely to have, expanded with
   `os.path.expanduser` / `pathlib.Path.home()`:
   `~/agent-memory-lite`, `~/work/agent-memory-lite`,
   `~/code/agent-memory-lite`, `~/projects/agent-memory-lite`,
   `~/Documents/agent-memory-lite`, `~/src/agent-memory-lite`.
   On Windows also try `~/Desktop/work/agent-memory-lite` and
   `~/Desktop/agent-memory-lite`.
4. A bounded `find ~ -name "agent-memory-lite" -type d -maxdepth 5`
   (or PowerShell `Get-ChildItem -Path $HOME -Recurse -Directory -Depth 5
   -Filter "agent-memory-lite" -ErrorAction SilentlyContinue`).

If REPO_ROOT cannot be found, stop and report:

> I cannot locate the agent-memory-lite checkout. Set the
> `AGENT_MEMORY_LITE_HOME` environment variable to its path, or tell me
> the absolute path here, and I will re-run.

Do not improvise a partial setup.

VENV_PYTHON = `<REPO_ROOT>/.venv/Scripts/python.exe` on Windows, or
`<REPO_ROOT>/.venv/bin/python` on macOS/Linux. If neither exists, fall
back to whichever `python` is on PATH and note the deviation in the final
report.

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

b) The tool is not in your tool list (you cannot call it) — every MCP
   client (Claude Code, Codex, Cursor, Continue, custom IDE plugins…)
   reads its MCP config file at startup, not on every prompt. The new
   entry will not be visible until the user restarts the runtime they
   are using. Identify which runtime is hosting you (Claude Code / Codex /
   Cursor / other — infer from the system prompt, available tools, or
   environment if you can; if unsure, say "the AI runtime you are using"
   in plain language). Report this restart as the only required manual
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
mcp tools:  <verified | NOT VISIBLE — please restart your runtime (<runtime name>)>

What this means for our future chats in this project:
- Before non-trivial work I will call memory_get_context to load prior context.
- After meaningful actions I will call memory_ingest_episode.
- After architectural choices I will call memory_write_decision.
- After task progress I will call memory_update_task_state.
All of this writes to <project>/.agent_memory/memory.db only — no
cross-project leakage.

Now tell me what you would like to do.
```

If MCP tools were not visible in Step 4, append (substitute the actual
runtime name — Claude Code / Codex / Cursor / "your AI runtime" if unsure):

```
ACTION REQUIRED: close and reopen <RUNTIME_NAME> so it picks up the new
MCP config. The config file you wrote is:
  - <PROJECT_ROOT>/.claude/settings.json    (Claude Code reads this)
  - <PROJECT_ROOT>/AGENTS.md                (Codex reads cwd AGENTS.md)
  - any per-project MCP config your runtime expects (check its docs)
After restart, the memory tools will be live.
```

Note on Codex specifically: Codex reads MCP servers from
`~/.codex/config.toml`, not from the project directory. If the user is on
Codex and you have not yet seen `agent-memory-lite` in your tool list,
recommend that they run from the agent-memory-lite repo:

```
python scripts/setup_agent.py
```

(without `--project`) so the global Codex config gets the entry. Then
restart Codex.
