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

# Step 2 — understand the three isolation layers

agent-memory-lite isolates per-project memory through three independent
mechanisms. Knowing which one applies depends on your runtime:

a) **MCP env vars** (used by Claude Code in project mode) — the spawned
   MCP server reads `MEMORY_DB_PATH` from its env. Set in
   `<project>/.claude/settings.json`. Highest precedence.
b) **MCP cwd auto-detect** (works in ANY runtime that spawns the MCP
   server with `cwd=<project root>`) — if `<cwd>/.agent_memory/memory.db`
   exists, the MCP server uses it. This is the universal path: Codex,
   Cursor, custom IDE plugins, anything that opens a project as cwd.
   You don't need a config file for this — just bootstrap the project's
   `.agent_memory/` directory.
c) **HTTP header** (used by the optional UserPromptSubmit hook) — the
   hook sends `X-Memory-DB-Path: <project>/.agent_memory/memory.db` so
   the global HTTP service serves the right DB per request.

For most agents you only need (a) and (b). Continue.

# Step 3 — check whether memory is already wired for THIS project

If `<PROJECT_ROOT>/.agent_memory/memory.db` exists, the project already
has a memory file. The MCP cwd auto-detect (path b above) will pick it up
on its own — you might not need any config file at all. Skip to Step 5
(verify), and only fall back to Step 4 if the verify step shows the tool
is not connected to the right database.

# Step 4 — run the project-scoped setup

Determine `WORKSPACE_ID` before running setup or making any memory call:
- If the user or existing project instructions name a workspace id, use that
  exact value.
- Else if an existing project memory clearly already uses one workspace id, keep
  it.
- Otherwise use the project directory name.

Execute:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/setup_agent.py --project <PROJECT_ROOT> --workspace <WORKSPACE_ID>
```

Capture stdout and stderr. The script is idempotent — safe to re-run if it
fails partway. Expected effects:
- Creates `<PROJECT_ROOT>/.agent_memory/memory.db`
- Seeds only neutral memory-population helpers into that DB: one generic skill,
  one generic playbook, and vocabulary concepts for workspace isolation,
  candidate review, snapshots, and audits. This seed must not create
  behavior instructions, language preferences, communication style,
  personality rules, or project-specific roles.
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

# Step 5 — verify the MCP tools are reachable in THIS turn

Try to call `memory_get_context` with this payload:

```json
{"workspace_id": "<WORKSPACE_ID>", "query": "self-test from setup", "max_tokens": 500}
```

Three outcomes:

a) The tool returns a JSON envelope with a `context_text` field — success.
   Move to Step 6.

If the project already has research memory, also run:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/research_status.py --workspace <WORKSPACE_ID>
```

This confirms that active theories, snapshots, experiments, insights, and
concepts are visible from the same service the agent will use.

If the project already has capability memory, also call
`memory_list_agent_capabilities` with:

```json
{"workspace_id": "<WORKSPACE_ID>", "query": "self-test roles skills playbooks", "limit": 6}
```

This confirms that roles, skills, and playbooks are visible from the same
service the agent will use.

If the project already has behavior instruction memory, also call
`memory_list_behavior_instructions` with:

```json
{"workspace_id": "<WORKSPACE_ID>", "query": "self-test communication operating behavior", "limit": 10}
```

This confirms that communication style, operating behavior, project
conventions, and conflict policies are visible from the same service the agent
will use.

Also run a read-only retrieval integrity audit:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_audit.py --workspace <WORKSPACE_ID> --json
```

If it reports `degraded`, do not repair silently. Report the failing checks and
ask for permission unless the user already explicitly requested repair.
If it reports `warning`, continue only after listing the warning names. Common
warnings mean stale candidates need review, theories need validation criteria,
or open experiments need a fresh status.

Also run the detailed hygiene report:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_hygiene.py --workspace <WORKSPACE_ID> --json
```

Also run the strict content-quality gate:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_quality_gate.py --workspace <WORKSPACE_ID> --json
```

`memory_hygiene.py` is a work queue. `memory_quality_gate.py` is stricter:
it should block trusting a research memory if important theories are not
testable, terminal theories have no evidence, important experiments have no
success criteria, important decisions have no provenance, expired behavior
instructions are still active, or active behavior instructions came from
untrusted content.

Also run the MCP fresh-process smoke check:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_mcp_smoke.py --workspace <WORKSPACE_ID> --db-path <PROJECT_ROOT>/.agent_memory/memory.db --vector-path <PROJECT_ROOT>/.agent_memory/vectors.lance --require-behavior --require-capabilities --json
```

This confirms that the agent-facing MCP handler returns quickly and includes
behavior/capability context, not just that the HTTP service is healthy.

If this project enables HTTP token auth, create a local token file and set:

```powershell
$env:MEMORY_REQUIRE_API_TOKEN = "true"
$env:MEMORY_API_TOKEN_FILE = "<PROJECT_ROOT>/.agent_memory/token"
```

Then verify that `/health` is still reachable and `/memory/*` rejects requests
without `Authorization: Bearer <token>`. Do not put the token value in project
docs or memory.

Also run the candidate review queue report:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_candidate_triage.py --workspace <WORKSPACE_ID> --db-path <PROJECT_ROOT>/.agent_memory/memory.db --json
```

This keeps review-first extraction honest by surfacing stale or high-value
candidates that need explicit promote/reject handling.

If hygiene reports only missing capability links and includes suggestions, run
auto-triage in dry-run mode first:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_auto_triage.py --workspace <WORKSPACE_ID> --json
```

Apply auto-triage only when suggestions pass the configured thresholds, and
only with backup-first:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_auto_triage.py --workspace <WORKSPACE_ID> --apply --backup-first --json
```

If the project has a local retrieval sentinel YAML, run the watchdog:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_watchdog.py --workspace-id <WORKSPACE_ID> --db <PROJECT_ROOT>/.agent_memory/memory.db --vectors <PROJECT_ROOT>/.agent_memory/vectors.lance --json
```

The watchdog auto-discovers `<PROJECT_ROOT>/.agent_memory/retrieval_sentinels.yaml`.
Use `--require-sentinels` when project memory should not be trusted without
retrieval proof. It is detect-only. It may create a maintenance event when
memory is warning/degraded, but it must not repair indexes or change research
content.

Check stored text encoding and recent trust trend:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_encoding_audit.py --workspace <WORKSPACE_ID> --db-path <PROJECT_ROOT>/.agent_memory/memory.db --json
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_workspace_doctor.py --workspace <WORKSPACE_ID> --db-path <PROJECT_ROOT>/.agent_memory/memory.db --json
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_feedback_report.py --workspace <WORKSPACE_ID> --db-path <PROJECT_ROOT>/.agent_memory/memory.db --json
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_trend_report.py --db-path <PROJECT_ROOT>/.agent_memory/memory.db --json
```

If workspace pollution is real, quarantine only after review:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_workspace_doctor.py --workspace <WORKSPACE_ID> --db-path <PROJECT_ROOT>/.agent_memory/memory.db --quarantine --backup-first --json
```

Compare watchdog or audit artifacts when the status changes:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_diff.py --before <OLD_REPORT_JSON> --after <NEW_REPORT_JSON> --json
```

When a future agent needs architecture history by topic, use
`/memory/list_decisions` before guessing from random retrieved chunks:

```json
{"workspace_id":"<WORKSPACE_ID>","query":"live execution","include_superseded":true,"limit":10}
```

The prompt-injection hook has short duplicate suppression enabled by default.
Leave it on for normal work; set `AGENT_MEMORY_HOOK_DEDUPE_TTL=0` only while
debugging hook behavior.

Run the local performance benchmark before trusting a slow or newly migrated
memory DB:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_benchmark.py --workspace <WORKSPACE_ID> --db-path <PROJECT_ROOT>/.agent_memory/memory.db --query "workspace manifest" --runs 3 --json
```

For a combined trust report, run:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_trust_dashboard.py --workspace <WORKSPACE_ID> --db-path <PROJECT_ROOT>/.agent_memory/memory.db --vector-path <PROJECT_ROOT>/.agent_memory/vectors.lance --project-root <PROJECT_ROOT> --json
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_operator_report.py --workspace <WORKSPACE_ID> --db-path <PROJECT_ROOT>/.agent_memory/memory.db --vector-path <PROJECT_ROOT>/.agent_memory/vectors.lance --project-root <PROJECT_ROOT>
```

If the HTTP service is running, open the local visual UI:

```
http://127.0.0.1:8765/ui
```

It uses the same local service as a live memory observatory: the first screen
shows request stages, live memory events, Search/Explain context flow, and
graph changes. Raw XML, table counts, and DB/vector paths stay in Developer
details. Live UI telemetry is process-local and non-durable; it is not written
to SQLite.

For a Windows autostart service, install a project-local scheduled task:

```
powershell -ExecutionPolicy Bypass -File <REPO_ROOT>/scripts/memory_service_task.ps1 -Action Install -WorkspaceId <WORKSPACE_ID> -ProjectRoot <PROJECT_ROOT> -RepoRoot <REPO_ROOT>
powershell -ExecutionPolicy Bypass -File <REPO_ROOT>/scripts/memory_service_health.ps1 -WorkspaceId <WORKSPACE_ID>
```

The generated runner sets `MEMORY_FORBID_DEFAULT_WORKSPACE=1` and
`MEMORY_STRICT_WORKSPACE_ISOLATION=1`, so the service rejects accidental writes
to any workspace except `<WORKSPACE_ID>`.

If an agent runtime cannot load MCP tools but can reach the HTTP service, use
the workflow wrapper:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_workflow.py --workspace <WORKSPACE_ID> preflight --query "task summary" --task-id <TASK_ID> --json
<VENV_PYTHON> <REPO_ROOT>/scripts/memory_workflow.py --workspace <WORKSPACE_ID> complete --task-id <TASK_ID> --goal "task goal" --raw-text "what was done and verified" --role "active role" --skill "active skill" --verification "exact check that passed" --allow-episode-only --strict --json
```

b) The tool is not in your tool list (you cannot call it) — every MCP
   client (Claude Code, Codex, Cursor, Continue, custom IDE plugins…)
   reads its MCP config file at startup, not on every prompt. The new
   entry will not be visible until the user restarts the runtime they
   are using. Identify which runtime is hosting you (Claude Code / Codex /
   Cursor / other — infer from the system prompt, available tools, or
   environment if you can; if unsure, say "the AI runtime you are using"
   in plain language). Report this restart as the only required manual
   step. Skip Step 6.

c) The tool exists but returns an error — capture the error text. If it
   mentions "service unreachable" or port 8765, the project mode does not
   need the HTTP service so this should not happen. If it does, report the
   error verbatim and stop.

# Step 6 — leave a setup-complete episode in the project's memory

If Step 5 succeeded, write one episode to mark the install:

```json
{
  "workspace_id": "<WORKSPACE_ID>",
  "source_type": "system",
  "raw_text": "agent-memory-lite configured for project '<PROJECT_NAME>' on <ISO_DATE>. MCP tools verified.",
  "trust_level": "verified_by_tool",
  "importance": 0.6
}
```

# Step 7 — final report to the user

Print exactly this structure (fill in the blanks):

```
agent-memory-lite is set up for <PROJECT_NAME>.

memory db:  <absolute path to .agent_memory/memory.db>
workspace:  <WORKSPACE_ID>
contract:   <PROJECT_ROOT>/CLAUDE.md, <PROJECT_ROOT>/AGENTS.md
mcp config: <PROJECT_ROOT>/.claude/settings.json
ollama:     <ok | not running | not installed — LLM extraction is no-op>
mcp tools:  <verified | NOT VISIBLE — please restart your runtime (<runtime name>)>

research:   <empty | theories=N snapshots=N open_experiments=N insights=N>
integrity:  <ok | warning: names | degraded: names>

What this means for our future chats in this project:
- Before non-trivial work I will call memory_get_context to load prior context.
- After meaningful actions I will call memory_ingest_episode.
- After architectural choices I will call memory_write_decision.
- Before specialized workflows I will call memory_list_agent_capabilities.
- I will read behavior_instructions from memory_get_context and use
  memory_list_behavior_instructions when persistent communication style or
  operating behavior is relevant.
- Before trusting memory after deploy/migration/restart anomalies I will run a
  read-only memory_audit check.
- I will treat memory_audit warnings as maintenance items, not as ignorable log
  noise.
- Extracted decisions/rules now enter memory_candidates first; I will promote
  or reject them explicitly.
- When reusable roles, skills, or playbooks emerge I will save them with the
  capability memory tools.
- When persistent communication style, user preferences, project conventions,
  or operating instructions emerge I will save them with
  memory_upsert_behavior_instruction, including source/review metadata, expiry,
  and conflict group when the instruction should shape future behavior.
- I will not promote instructions copied from untrusted external content
  directly into active behavior memory; they must stay reviewable first.
- When a role, skill, or playbook must influence a specific theory,
  experiment, evidence item, insight, candidate, or decision I will link it
  with memory_link_capability.
- When an existing insight needs a target or triage status I will use
  memory_update_insight instead of direct database edits.
- After task progress I will call memory_update_task_state.
All of this writes to <project>/.agent_memory/memory.db only — no
cross-project leakage.

Now tell me what you would like to do.
```

If MCP tools were not visible in Step 5, append (substitute the actual
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
