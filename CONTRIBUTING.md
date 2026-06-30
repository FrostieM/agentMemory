# Contributing to agent-memory-lite

A local-first memory service for AI coding agents. SQLite is the source of record,
LanceDB stores vectors, sentence-transformers does local embeddings, Ollama is the
optional local LLM extractor. The service binds to `127.0.0.1:8765` only.

## Setup

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env            # all settings + defaults; nothing is required
```

## Hard rules

- **Active agent surface is v3-only.** Do not add new legacy/v2 MCP names or
  compatibility shims. Legacy references stay only in historical changelogs and
  migrations. (Gated by `scripts/v3_surface_check.py`.)
- **Forward-only SQL migrations.** Add `migrations/NNNN_*.sql`; never edit a
  shipped migration. Update the migration-list assertions in
  `tests/unit/db/test_migrations.py`.
- **Small, focused modules.** `src/agent_memory_lite/**/*.py` stays at or below
  **150 SLOC**, one concern per module. A new oversized file FAILs CI; a
  grandfathered file may only **shrink** (a ratchet — see
  `scripts/check_sloc.py`). Split, don't grandfather.
- **Paired tests for non-trivial behavior.** Prefer a test that fails on the
  regression it guards (a "mutation probe" mindset — a test that can't fail is
  worthless).
- **English** for code, comments, commits, and docs (the v1.10 correction modules
  carry intentional bilingual content; everything else is English + ASCII).
- **Never store secrets.** Server-side redaction helps; do not defeat it.
- **Preserve strict workspace isolation.** Writes to foreign workspaces are
  blocked; reads to registered workspaces are allowed.
- **New env var?** Add its `validation_alias` to `Settings`, then run
  `python scripts/check_env_docs.py --write` to document it in `.env.example`.

## The local bar (run before every commit)

CI runs all of these; run them locally first — the pre-push hook only runs the
crash test, so the rest of the bar can drift red silently.

```bash
ruff check src tests scripts && ruff format --check src tests scripts
mypy src
python scripts/check_sloc.py --enforce          # 150-SLOC ratchet
python scripts/v3_surface_check.py              # v3-only surface
python scripts/check_env_docs.py --enforce      # env-var docs
python scripts/check_encoding.py --enforce      # UTF-8 / mojibake
pytest -q
python scripts/run_evals.py --workspace default --no-vector   # recall@10=1.0, secret_leak=0
python scripts/golden_eval.py --compare-baseline              # MRR/NDCG@10 ranking gate
python -m scripts.crash_test --skip-ui --skip-llm             # 27-phase reliability gate
```

## Architecture

```text
api/routes/      HTTP wire layer
ingestion/       write pipelines
retrieval/       read pipelines
cognition/       brief, lint, impact, self-model, consolidation
maintenance/     background loops ("brain pass") and quality checks
repositories/    SQL wrappers
storage/         v3 compact read/write surface
db/              connection and migrations
mcp/             v3 stdio transport and 12-tool registry
```

## Commit / PR conventions

- Conventional-commit prefixes (`feat:`, `fix:`, `test:`, `chore:`, `docs:`).
- One logical change per commit; keep the diff reviewable.
- A PR must pass the full CI bar above (including the `reliability` job).
