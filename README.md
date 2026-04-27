# agent-memory-lite

Local memory subsystem for an AI agent. Runs from a virtualenv. No Docker, no Postgres,
no cloud LLM/vector providers.

```
Agent ──HTTP / MCP / in-process──▶ Memory Service (FastAPI on 127.0.0.1:8765)
                                    │
                                    ├─ SQLite (WAL, FTS5)   ── episodes, chunks, decisions,
                                    │                          task_state, core_memory,
                                    │                          procedural_rules, entities,
                                    │                          facts, audit_log
                                    ├─ LanceDB              ── embedded vector store
                                    ├─ sentence-transformers── intfloat/multilingual-e5-small (default)
                                    └─ Ollama (mandatory)   ── qwen2.5:7b-instruct for extraction
```

## Status

v1 feature-complete: all six phases of the spec are landed (episodes + FTS,
hybrid retrieval, decisions/task/core/procedural, lite temporal graph, file
ingestion, compaction + evals + MCP tool registry). 246 tests pass; ruff +
mypy strict are clean. See `SESSION_STATE.md` for details.

## Requirements

- **Python 3.13 recommended** (3.12 supported, 3.14 supported but `torch` wheels may lag).
- **Ollama** required for production LLM-driven extraction. The service refuses to start
  unless Ollama responds at `LLM_BASE_URL`, **or** `OLLAMA_PROBE_SKIP=true` (use the
  skip flag for tests / first-run smoke checks only).
- Windows / macOS / Linux. Paths are normalized; tested on Windows.

## Install

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Unix:    source .venv/bin/activate

pip install -e ".[dev]"
```

If `torch` wheels are unavailable for your Python version, install Python 3.13 alongside
(via `pyenv-win`, `rye`, or the official installer) and recreate the venv.

## Set up Ollama (recommended for full feature set)

```bash
# https://ollama.com/download
ollama pull qwen2.5:7b-instruct
```

The default `LLM_BASE_URL` is `http://127.0.0.1:11434`. Without Ollama, set
`OLLAMA_PROBE_SKIP=true` in `.env` and the heuristic extractor still runs;
LLM-driven candidate extraction is simply disabled until Ollama is reachable.

## Quick verification (≈ 2 minutes, no Ollama needed)

This is the end-to-end check the project ships with. It boots the service,
seeds the workspace with a representative session, exercises every public
endpoint, and runs the eval harness.

```bash
# 1. configure (one-off)
cp .env.example .env
# Either install Ollama (see above) or skip the probe:
sed -i 's/^OLLAMA_PROBE_SKIP=false/OLLAMA_PROBE_SKIP=true/' .env

# 2. unit + property + integration + e2e tests (~3s)
pytest -q

# 3. start a fresh DB and the service
rm -rf .agent_memory
python scripts/bootstrap_db.py
python -m agent_memory_lite          # binds 127.0.0.1:8765 in this terminal

# 4. in a second terminal, smoke-test every route end-to-end:
python scripts/seed_demo_session.py
```

`scripts/seed_demo_session.py` ingests 10 episodes (one with secrets that get
redacted in flight), writes 3 architectural decisions, upserts a task state,
queries `POST /memory/get_context` (the agent-facing surface), runs an exact
FTS lookup via `POST /memory/search`, and finally runs the eval harness via
`POST /memory/run_evals`. The expected outcome:

```
=== POST /memory/run_evals ===
{
  "cases_run": 10,
  "cases_passed": 10,
  "retrieval_recall_at_10": 1.0,
  "retrieval_precision_at_10": 0.75,
  "stale_fact_rate": 0.0,
  "secret_leak_count": 0,
  "prompt_injection_failures": 0,
  "failures": []
}
```

Health check at any time:

```bash
curl http://127.0.0.1:8765/health
```

To start over with a clean memory:

```bash
rm -rf .agent_memory
python scripts/bootstrap_db.py
```

## Test

```bash
pytest                  # unit + property + integration + e2e
pytest -m needs_ollama  # extraction tests against a live Ollama (opt-in)
```

## Paste-and-forget agent prompts

For the laziest possible setup, hand the agent one of two prompts and it
does the rest itself. See [`AGENT_SETUP/`](AGENT_SETUP/):

- [`01_FRESH_PROJECT.md`](AGENT_SETUP/01_FRESH_PROJECT.md) — paste in a new
  chat. Agent locates the agent-memory-lite repo, runs
  `setup_agent.py --project`, verifies MCP tools, leaves a setup-complete
  episode. No follow-up questions to you.
- [`02_CAPTURE_THIS_CHAT.md`](AGENT_SETUP/02_CAPTURE_THIS_CHAT.md) — paste
  in a chat that already has work in it. Agent ensures memory is wired,
  walks the conversation, persists task state + decisions + episodes, and
  verifies by querying back.

For the manual setup commands behind those prompts, see below.

## Make agents use this memory persistently

A one-shot prompt does not persist between sessions. Run **one command per
project** and every AI agent that opens that project will load the memory
contract, see the memory tools as native tool calls, and (for Claude Code)
get memory context auto-injected before every prompt.

### Per-project memory (recommended for multiple projects)

Each project gets its own isolated memory — no cross-project leakage.

```bash
cd /path/to/your/project
python /path/to/agent-memory-lite/scripts/setup_agent.py --project
```

Writes:
- `<project>/.claude/settings.json` — MCP server entry whose `env` pins
  `MEMORY_DB_PATH` and `VECTOR_DB_PATH` to `<project>/.agent_memory/`.
- `<project>/CLAUDE.md` and `<project>/AGENTS.md` — the agent contract
  (Claude Code reads CLAUDE.md, Codex reads AGENTS.md).
- `<project>/.agent_memory/memory.db` — bootstrapped fresh.

**Project isolation works on any runtime.** The MCP server has three
ways to find the right database, in this order of precedence:

1. **`MEMORY_DB_PATH` env var** in the MCP server config — Claude Code
   project mode uses this (written by `setup_agent.py --project` into
   `<project>/.claude/settings.json`). Highest precedence.
2. **`<cwd>/.agent_memory/memory.db` auto-detect** — works on every
   runtime that spawns the MCP server with the project as cwd. Codex,
   Cursor, custom IDE plugins, anything — no per-runtime config file
   needed, just bootstrap `.agent_memory/` in each project.
3. **Defaults from `.env`** in the agent-memory-lite repo. Used when the
   MCP server is launched outside any project.

So when you open project A in any runtime, the spawned MCP server sees
only A's memory. Open project B, you get only B's. The optional HTTP
hook (Claude Code) carries the same isolation via the
`X-Memory-DB-Path` header that the project-scoped hook command sends.

### Global memory (one shared pool across all projects)

```bash
python scripts/setup_agent.py
```

Writes to `~/.claude/`, `~/.codex/`, `~/.cursor/`. Useful when you
explicitly want one memory pool everywhere on the machine. Comes with the
Claude Code `UserPromptSubmit` hook that auto-injects `<memory_context>`
before every prompt (see `scripts/inject_memory_context.py`).

The script (either mode) is idempotent. It:

1. Verifies the venv has `agent-memory-lite` + the `[mcp]` extra installed.
2. Detects Ollama (binary, daemon, `qwen2.5:7b-instruct`) and the memory db.
3. Bootstraps the database if missing.
4. Sets `OLLAMA_PROBE_SKIP` based on whether Ollama is reachable.
5. For every agent runtime present on the machine, writes:
   - **Claude Code** (`~/.claude/`):
     `settings.json` MCP server entry + `CLAUDE.md` contract +
     `UserPromptSubmit` hook (calls `scripts/inject_memory_context.py`,
     which prepends `<memory_context>` to every user prompt).
   - **Codex** (`~/.codex/`):
     `config.toml` MCP server entry + `AGENTS.md` contract.
   - **Cursor** (`~/.cursor/`):
     `mcp.json` MCP server entry + `rules/agent-memory-lite.md` contract.
6. Emits a generic JSON snippet for any other MCP-aware agent.
7. Smoke-tests the MCP stdio server (initialize + tools/list).

After this, in any new chat the agent has three layers of "don't forget":

- **Tools layer**: `memory_get_context`, `memory_search`,
  `memory_ingest_episode`, `memory_write_decision`,
  `memory_update_task_state`, `memory_ingest_file` appear in the tool list
  natively (via MCP), no system prompt required.
- **Instructions layer**: the contract markdown is auto-loaded into the
  agent's system context every session.
- **Auto-injection layer** (Claude Code only): the hook calls the HTTP
  service for every user prompt and prepends a `<memory_context>` block,
  so the agent sees relevant memory **before** it decides whether to call
  any tools.

Re-run `python scripts/status.py` at any time to see the current state.

Flags:
- `--check-only` — diagnose only, no writes.
- `--no-hook` — skip the Claude Code hook (tools + contract still installed).

### What you still need to start manually

The MCP stdio server boots per agent session — no separate process required
for tools to work. The **HTTP service** is what backs the auto-injection
hook and any non-MCP client. One command from the repo root:

```cmd
.\start.bat            (Windows)
./start.sh             (macOS / Linux / Git Bash)
```

The launchers auto-detect the project venv (`.venv/Scripts/python.exe` or
`.venv/bin/python`), bootstrap the DB if missing, refuse to start when
port 8765 is already taken, and run `python -m agent_memory_lite` in the
foreground (Ctrl+C to stop). Override the port with
`AGENT_MEMORY_PORT=<n>`.

To keep it running across reboots: put `.\start.bat` in a Windows startup
folder, drop `./start.sh` into a launchd plist, a systemd user service,
or whatever your OS prefers.

### The contract behind it all

`docs/AGENT_CONTRACT.md` is the canonical instruction text. Setup writes
it into each runtime's "always-loaded" file — you can edit it once and
rerun setup to push the new version everywhere.

## Workspace ingestion

Index a whole project tree (respects `.gitignore` + builtin denylist + optional
`.memoryignore`):

```bash
python scripts/ingest_workspace.py --workspace default --path /path/to/repo
```

## Reindex vectors

When you change the embedding model or restore from a backup that lost LanceDB:

```bash
python scripts/reindex_vectors.py
```

## Local-only enforcement

At startup, every `*_BASE_URL` setting is parsed and rejected if the host is not
`127.0.0.1` / `localhost` / `::1`, or if it matches a cloud provider denylist
(`api.openai.com`, `api.anthropic.com`, `api.cohere.com`, …).
Cloud LLM SDKs (`openai`, `anthropic`, `cohere`, …) are banned at lint time
via `ruff`'s `flake8-tidy-imports`.

To override the guard for a one-off (e.g. local development with a non-loopback host),
set both `LOCAL_ONLY=false` and `ALLOW_REMOTE_PROVIDERS=true`. **Do not** ship that
configuration.

## Project layout

See `CLAUDE.md` for the layered architecture and `docs/` for design notes.
Source files are capped at ~150 SLOC; concerns the spec collapses into one
file (`retrieval.py`, `graph.py`, `extraction.py`, `chunking.py`,
`redaction.py`) live as subpackages.

## License

MIT.
