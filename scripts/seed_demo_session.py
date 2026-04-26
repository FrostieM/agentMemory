"""End-to-end smoke check via the running HTTP service.

Seeds the workspace with a representative session (episodes, decisions, task
state, secrets-bearing text), then exercises:

- POST /memory/ingest_episode
- POST /memory/write_decision
- POST /memory/update_task_state
- POST /memory/get_context
- POST /memory/search (mode=fts)
- POST /memory/run_evals

Prerequisite: the service is already running on http://127.0.0.1:8765.

    python scripts/bootstrap_db.py
    python -m agent_memory_lite          # in another terminal
    python scripts/seed_demo_session.py
"""

from __future__ import annotations

import json
import sys

import httpx

BASE = "http://127.0.0.1:8765"
WORKSPACE = "default"
TASK_ID = "agent-memory-lite-demo"


EPISODES: list[dict[str, object]] = [
    {
        "raw_text": (
            "User asked me to build a local memory subsystem for an AI agent. "
            "Stack: SQLite (WAL+FTS5) + LanceDB + sentence-transformers + Ollama; "
            "no Docker, no Postgres, no cloud providers."
        ),
        "trust_level": "user_asserted",
        "importance": 0.95,
    },
    {
        "raw_text": (
            "Phase 0 shipped: pyproject (Python 3.12-3.14), forward-only SQL "
            "migrations 0001_init (12 tables + indexes) and 0002_chunks_fts "
            "(FTS5 virtual table), FastAPI app on 127.0.0.1:8765, local-only "
            "guard with cloud denylist + telemetry kill list, 49 tests."
        ),
        "trust_level": "agent_observed",
        "importance": 0.8,
    },
    {
        "raw_text": (
            "Phase 1 shipped: redaction subpackage with property tests on "
            "idempotence and secret coverage, sentence-transformers embedding "
            "provider, BM25 FTS query, episode pipeline."
        ),
        "trust_level": "agent_observed",
        "importance": 0.8,
    },
    {
        "raw_text": (
            "Phase 2 shipped: VectorStore Protocol with LanceDB default + "
            "sqlite-vec opt-in. Retrieval pipeline does FTS+vector candidates -> "
            "reciprocal rank fusion (k=60) -> weighted scoring -> token budget "
            "-> XML envelope. POST /memory/get_context."
        ),
        "trust_level": "agent_observed",
        "importance": 0.8,
    },
    {
        "raw_text": (
            "Phase 3 shipped: decisions with supersedes-chain, task_state upsert, "
            "core_memory promotion, procedural rules, extraction layer (heuristic "
            "+ Ollama LLM extractor + thresholds + trust_gate)."
        ),
        "trust_level": "agent_observed",
        "importance": 0.85,
    },
    {
        "raw_text": (
            "Phase 4 shipped: lite temporal graph. Entities and facts with "
            "valid_from/valid_to/invalidated_by_fact_id. Conflict invalidation "
            "in one transaction. Bounded BFS up to 2 hops / 40 facts."
        ),
        "trust_level": "agent_observed",
        "importance": 0.8,
    },
    {
        "raw_text": (
            "Phase 5 shipped: file ingestion. Symbols via Python ast + regex "
            "fallback, code chunking by AST, markdown by heading, "
            ".gitignore + builtin denylist + .memoryignore overlay, idempotent "
            "by content_hash, POST /memory/ingest_file."
        ),
        "trust_level": "agent_observed",
        "importance": 0.8,
    },
    {
        "raw_text": (
            "Phase 6 shipped: compaction (summarize_old, archive_stale, "
            "promote_durable), eval harness with recall@K + precision@K, YAML "
            "fixtures, MCP tool registry."
        ),
        "trust_level": "agent_observed",
        "importance": 0.85,
    },
    {
        "raw_text": (
            "Final: 246 tests passing, ruff + mypy strict clean across 136 "
            "source files. All six phases pushed to "
            "git@github.com:FrostieM/agentMemory.git on branch main."
        ),
        "trust_level": "agent_observed",
        "importance": 0.9,
    },
    {
        "raw_text": (
            "Redaction demo: password=hunter2 token=ghp_aaaaaaaaaaaaaaaaaaaaaa"
            "aaaaaaaaaaaa01234567 api_key=sk-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            "AAAAAAAAAAAA. The episode below should arrive with each secret "
            "replaced by a <<REDACTED:KIND>> marker."
        ),
        "trust_level": "agent_observed",
        "importance": 0.5,
    },
]


DECISIONS: list[dict[str, object]] = [
    {
        "title": "Lite memory architecture",
        "decision_text": (
            "Use SQLite (WAL+FTS5) as source of record, LanceDB for embedded "
            "vector search (sqlite-vec opt-in), sentence-transformers for "
            "embeddings, Ollama for LLM extraction. Local-only at startup."
        ),
        "rationale": "No Docker, no Postgres, no cloud providers per user requirement.",
        "confidence": 0.95,
        "importance": 0.95,
    },
    {
        "title": "Default embedding model",
        "decision_text": (
            "intfloat/multilingual-e5-small via sentence-transformers (384 dim, CPU)."
        ),
        "rationale": "Mixed RU/EN content. Pinned per workspace via workspace_meta.",
        "confidence": 0.92,
        "importance": 0.85,
    },
    {
        "title": "Single-workspace v1",
        "decision_text": (
            "Hard-code workspace_id='default' at the API surface; the column "
            "stays in every table so multi-workspace is a future API change."
        ),
        "rationale": "Reduces v1 surface area; the column makes upgrades trivial.",
        "confidence": 0.9,
        "importance": 0.7,
    },
]


TASK_STATE: dict[str, object] = {
    "task_id": TASK_ID,
    "goal": "Build the no-Docker lite memory subsystem from the spec.",
    "status": "done",
    "current_plan": [
        "Phase 0: bootstrap",
        "Phase 1: episodes + FTS + redaction",
        "Phase 2: vector store + hybrid retrieval",
        "Phase 3: decisions + task state + extraction",
        "Phase 4: lite temporal graph",
        "Phase 5: file/project ingestion",
        "Phase 6: compaction + evals + MCP",
    ],
    "completed_steps": ["Phase 0", "Phase 1", "Phase 2", "Phase 3", "Phase 4", "Phase 5", "Phase 6"],
    "next_action": None,
    "blockers": [],
    "files_in_scope": ["src/agent_memory_lite/", "tests/", "migrations/", "scripts/"],
}


def _post(client: httpx.Client, path: str, payload: dict[str, object]) -> dict[str, object]:
    response = client.post(f"{BASE}{path}", json=payload)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise RuntimeError(f"unexpected response body for {path}: {body!r}")
    return body


def _seed_episodes(client: httpx.Client) -> list[str]:
    ids: list[str] = []
    for episode in EPISODES:
        payload: dict[str, object] = {
            "workspace_id": WORKSPACE,
            "task_id": TASK_ID,
            "source_type": "agent_action",
            **episode,
        }
        result = _post(client, "/memory/ingest_episode", payload)
        ids.append(str(result["episode_id"]))
        kinds = result.get("redacted_kinds") or []
        if kinds:
            print(f"  redacted in {result['episode_id']}: {kinds}")
    return ids


def _seed_decisions(client: httpx.Client) -> None:
    for decision in DECISIONS:
        _post(client, "/memory/write_decision", {"workspace_id": WORKSPACE, **decision})


def _seed_task(client: httpx.Client) -> None:
    _post(client, "/memory/update_task_state", {"workspace_id": WORKSPACE, **TASK_STATE})


def _query_context(client: httpx.Client) -> dict[str, object]:
    return _post(
        client,
        "/memory/get_context",
        {
            "workspace_id": WORKSPACE,
            "task_id": TASK_ID,
            "query": "what was decided about embedding model and retrieval pipeline",
            "max_tokens": 3500,
        },
    )


def _query_fts(client: httpx.Client) -> dict[str, object]:
    return _post(
        client,
        "/memory/search",
        {
            "workspace_id": WORKSPACE,
            "query": "FTS5 virtual table 0002_chunks_fts",
            "mode": "fts",
            "limit": 5,
        },
    )


def _run_evals(client: httpx.Client) -> dict[str, object]:
    return _post(client, "/memory/run_evals", {"workspace_id": WORKSPACE})


def main() -> int:
    with httpx.Client(timeout=120.0) as client:
        print("=== ingesting episodes ===")
        episode_ids = _seed_episodes(client)
        print(f"  -> {len(episode_ids)} episodes")
        print("=== writing decisions ===")
        _seed_decisions(client)
        print(f"  -> {len(DECISIONS)} decisions")
        print("=== upserting task state ===")
        _seed_task(client)
        print("=== POST /memory/get_context ===")
        context = _query_context(client)
        print()
        print(context["context_text"])
        print()
        print("=== POST /memory/search (mode=fts) ===")
        print(json.dumps(_query_fts(client), indent=2)[:800])
        print()
        print("=== POST /memory/run_evals ===")
        print(json.dumps(_run_evals(client), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
