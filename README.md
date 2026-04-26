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

## Make agents use this memory persistently

A one-shot prompt does not persist between sessions. Pick one of these so the
agent loads the contract automatically every chat. The canonical contract
lives at [`docs/AGENT_CONTRACT.md`](docs/AGENT_CONTRACT.md).

### Option 1 — Claude Code via `CLAUDE.md` (recommended)

Claude Code reads `CLAUDE.md` at session start. Two scopes:

- **Per project**: copy `docs/AGENT_CONTRACT.md` into the project's `CLAUDE.md`
  (or append to it). Every Claude Code session opened in that repo will follow
  the contract.
- **Global**: copy it into `~/.claude/CLAUDE.md`. Every Claude Code session
  on the machine — across all repos — will follow it.

```bash
# Per-project (run from the consumer project, not from agent-memory-lite)
cat /path/to/agent-memory-lite/docs/AGENT_CONTRACT.md >> CLAUDE.md

# Global
cat /path/to/agent-memory-lite/docs/AGENT_CONTRACT.md >> ~/.claude/CLAUDE.md
```

### Option 2 — System prompt / developer message

For agents that take a system or developer prompt (Claude API, OpenAI-compatible
clients, custom loops): paste the contents of `docs/AGENT_CONTRACT.md` into the
system message at session start.

### Option 3 — MCP (future, partially shipped)

`agent_memory_lite.mcp.tools.TOOLS` is the canonical tool registry; the
`dispatch(name, **kwargs)` helper executes a tool against a live SQLite
connection. A real stdio JSON-RPC transport is not bundled yet — wire the
registry into your preferred MCP runtime when you want first-class tool
discovery in Claude Code or Cursor. For now Option 1 is the path of least
resistance.

### Option 4 — Manual paste (one-off)

For a single chat: paste the contract into the first user message. The agent
will follow it for that session and forget on the next one.

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
