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

Phase 0 (bootstrap). See `SESSION_STATE.md` for the current step.

## Requirements

- **Python 3.13 recommended** (3.12 supported, 3.14 supported but `torch` wheels may lag).
- **Ollama** must be installed and running locally. The service refuses to start otherwise.
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

## Set up Ollama

```bash
# https://ollama.com/download
ollama pull qwen2.5:7b-instruct
```

The default `LLM_BASE_URL` is `http://127.0.0.1:11434`.

## Run

```bash
cp .env.example .env          # then edit if needed
python scripts/bootstrap_db.py
python -m agent_memory_lite
```

The service binds to `http://127.0.0.1:8765`. Health check:

```bash
curl http://127.0.0.1:8765/health
```

## Test

```bash
pytest                  # unit + property + e2e (most without Ollama)
pytest -m needs_ollama  # only LLM-extraction tests; requires Ollama running
```

## Local-only enforcement

At startup, every `*_BASE_URL` setting is parsed and rejected if the host is not
`127.0.0.1` / `localhost` / `::1`, or if it matches a cloud provider denylist
(`api.openai.com`, `api.anthropic.com`, `api.cohere.com`, etc.).
Cloud LLM SDKs (`openai`, `anthropic`, `cohere`, …) are also banned at lint time
via `ruff`'s `flake8-tidy-imports`.

To override the guard for a one-off (e.g. local development with a non-loopback host),
set both `LOCAL_ONLY=false` and `ALLOW_REMOTE_PROVIDERS=true`. **Do not** ship that
configuration.

## Project layout

See `docs/architecture.md`. Source files are capped at ~150 SLOC; concerns the spec
collapses into one file (`retrieval.py`, `graph.py`, `extraction.py`, `chunking.py`,
`redaction.py`) live as subpackages.

## License

MIT.
