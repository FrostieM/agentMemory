"""Seed the `agentLight` workspace with the full audit dump.

This script writes the post-summary audit findings into the agent-memory-lite
project's own memory database so the running MCP server (configured with
`MEMORY_WORKSPACE_ID=agentLight` and `MEMORY_FORBID_DEFAULT_WORKSPACE=true`)
has a durable record of:

  * eight audit episodes (boot, hand-off, evolution, isolation, surface, fix log)
  * three architectural decisions (workspace name, isolation primitive, hook contract)
  * one task_state row reflecting the audit phase

It targets the running HTTP service on 127.0.0.1:8765 and uses the
`X-Memory-DB-Path` / `X-Memory-Vector-Path` headers to route to the
agent-memory-lite DB even if the global service has a different default
workspace.

Run with:

    .venv\\Scripts\\python.exe scripts\\agentlight_audit_seed.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / ".agent_memory" / "memory.db"
VEC_PATH = REPO_ROOT / ".agent_memory" / "vectors.lance"
BASE_URL = "http://127.0.0.1:8765"
WORKSPACE = "agentLight"
TASK_ID = "agentlight-audit-2026-05"

HEADERS = {
    "Content-Type": "application/json",
    "X-Memory-DB-Path": str(DB_PATH),
    "X-Memory-Vector-Path": str(VEC_PATH),
}

EPISODES: list[dict[str, Any]] = [
    {
        "title": "agentLight bootstrap",
        "raw_text": (
            "agentLight is the workspace_id for the agent-memory-lite project's own "
            "self-memory. Physical isolation: MEMORY_DB_PATH points at "
            "<repo>/.agent_memory/memory.db and VECTOR_DB_PATH points at "
            "<repo>/.agent_memory/vectors.lance. Logical isolation: "
            "workspace_id=agentLight everywhere (MCP env MEMORY_WORKSPACE_ID, "
            "UserPromptSubmit hook --workspace flag, every HTTP/MCP body)."
        ),
        "importance": 0.95,
    },
    {
        "title": "Hand-off audit baseline",
        "raw_text": (
            "Repo audit on 2026-05-01: ~200 source files under "
            "src/agent_memory_lite, 102 tests across unit/property/integration/e2e, "
            "54 commits since the initial bootstrap. Migrations 0001 through 0014 "
            "are applied: theories, research_lab, agent_capabilities, "
            "theory_discipline, memory_integrity_candidates, "
            "capability_research_links, workspace_manifest (with repair), "
            "behavior_instructions (with governance), vector_index_metadata, "
            "usage_feedback. Operational stack: SQLite WAL+FTS5 + LanceDB + "
            "sentence-transformers (intfloat/multilingual-e5-small, dim 384) + "
            "Ollama qwen2.5:7b-instruct."
        ),
        "importance": 0.85,
    },
    {
        "title": "New memory layers since the original spec",
        "raw_text": (
            "The codebase evolved past the original episodic+graph spec. New "
            "first-class objects: theories (with predictions, validation criteria, "
            "evidence, status), research_lab (snapshots, experiments, "
            "experiment_results, concepts, research_insights), agent_capabilities "
            "(roles, skills, playbooks, capability_links), behavior_instructions "
            "(communication style, operating rule, project convention, workflow "
            "preference, role guidance) with conflict_policy, scope, expiry, and "
            "governance fields. Episodes remain the audit log; theories are the "
            "research backlog; capabilities are the reusable execution knowledge."
        ),
        "importance": 0.85,
    },
    {
        "title": "Workspace isolation primitives",
        "raw_text": (
            "Two flags govern strict project mode. "
            "MEMORY_FORBID_DEFAULT_WORKSPACE=true rejects any request that falls "
            "back to workspace_id=default; setup_agent.py --project sets it. "
            "MEMORY_STRICT_WORKSPACE_ISOLATION=1 rejects any request whose "
            "workspace_id differs from MEMORY_WORKSPACE_ID. The path is the "
            "isolation primitive (each project has its own SQLite + LanceDB pair); "
            "workspace_id is the validation handle that the strict guard checks. "
            "Together they make cross-project leakage impossible by construction."
        ),
        "importance": 0.9,
    },
    {
        "title": "API surface evolution",
        "raw_text": (
            "New routes since the original spec: /memory/explain_context "
            "(retrieval explainability), /memory/list_decisions (topic-level "
            "decision lookup), /memory/quality_gate (research trust gate), "
            "/memory/hygiene_report (content-discipline findings with "
            "suggested_capability_links), /memory/record_usage_feedback (bounded "
            "ranking signal), /memory/ui (local browser observability), "
            "/memory/ui/events (SSE) and /memory/ui/state (polling fallback). "
            "Optional bearer-token auth on /memory/* via MEMORY_REQUIRE_API_TOKEN; "
            "/health stays unauthenticated for local monitoring."
        ),
        "importance": 0.8,
    },
    {
        "title": "Hook 400 root cause and fix",
        "raw_text": (
            "Symptom: UserPromptSubmit hook printed timeouts and 400 Bad Request. "
            "Cause: hook command sent workspace_id=default while the service ran "
            "with MEMORY_FORBID_DEFAULT_WORKSPACE=true. Fix: setup_agent.py --project "
            "now bakes --workspace <name> into the hook command and "
            "MEMORY_WORKSPACE_ID=<name> into the MCP env block. For agentLight the "
            "values are workspace_id=agentLight in both places. This makes the "
            "hook quiet on success and produces a single <agent-memory> notice on "
            "failure rather than crashing."
        ),
        "importance": 0.85,
    },
    {
        "title": "MCP stdout corruption fix",
        "raw_text": (
            "Symptom: MCP handshake completed but the client reported 'could not "
            "connect'. Cause: logging_setup.py used basicConfig(stream=sys.stdout) "
            "and sentence-transformers' 'No device provided, using cpu' message "
            "corrupted the JSON-RPC framing. Fix: switched the default stream to "
            "stderr with force=True, plus an import-time side effect at "
            "agent_memory_lite/mcp/__init__.py that calls "
            "logging.basicConfig(stream=sys.stderr, level=WARNING, force=True) "
            "before any third-party import. Stdout is reserved for protocol bytes."
        ),
        "importance": 0.85,
    },
    {
        "title": "Resumed session integrity fix",
        "raw_text": (
            "Power outage truncated 5 .jsonl chat session files in "
            "C:/Users/Osino/.claude/projects/C--Users-Osino-Desktop-work-copyBot, "
            "blocking session resume with 'JSON Parse error: Unrecognized token'. "
            "Repair: read each file line by line, kept only valid JSON lines, "
            "wrote the cleaned file back, dropped a .jsonl.bak alongside. One bad "
            "line dropped per file, ~1585 valid lines kept. The auto-memory "
            "subsystem at agent-memory-lite was unaffected."
        ),
        "importance": 0.7,
    },
]

DECISIONS: list[dict[str, Any]] = [
    {
        "title": "Workspace name agentLight",
        "decision_text": (
            "The agent-memory-lite project's own self-memory uses workspace_id "
            "= agentLight. Use this exact value everywhere: MCP env "
            "MEMORY_WORKSPACE_ID, UserPromptSubmit hook --workspace flag, every "
            "HTTP/MCP request body, and the strict-isolation guard."
        ),
        "rationale": (
            "Stable, distinct workspace name avoids collision with the historical "
            "default fallback and with copyBot's workspace. The value is short, "
            "memorable, and reads naturally in tool output."
        ),
        "importance": 0.95,
    },
    {
        "title": "Path is the isolation primitive",
        "decision_text": (
            "Per-project memory is isolated at the file-system layer via "
            "MEMORY_DB_PATH and VECTOR_DB_PATH. workspace_id is the logical "
            "namespace inside that physical pair and the handle the strict "
            "isolation guard validates. Cross-project leakage is impossible "
            "because two projects never see the same SQLite file."
        ),
        "rationale": (
            "A single DB with multiple workspace_ids would be a soft boundary "
            "that subtle bugs could break. Separate files cannot leak even with "
            "buggy code, and the workspace handle still gives us logical "
            "validation, retrieval scoping, and audit clarity."
        ),
        "importance": 0.9,
    },
    {
        "title": "Hook contract for project mode",
        "decision_text": (
            "UserPromptSubmit hooks invoked by setup_agent.py --project must "
            "pass --db-path, --vector-path, and --workspace flags that exactly "
            "match the MCP server's env. The hook converts those flags into "
            "X-Memory-DB-Path / X-Memory-Vector-Path headers and a workspace_id "
            "body field, so a global HTTP service can serve the project's hook "
            "without leaking to the wrong DB. On failure the hook prints an "
            "<agent-memory> notice and exits 0, so chat input is never blocked."
        ),
        "rationale": (
            "The hook runs out of process and cannot read MCP env. Encoding the "
            "three identifiers as CLI args keeps the contract explicit, "
            "auditable, and visible in .claude/settings.json. Failing soft "
            "(notice + exit 0) keeps the user productive when the service is "
            "down or restarting."
        ),
        "importance": 0.85,
    },
]


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = httpx.post(f"{BASE_URL}{path}", json=payload, headers=HEADERS, timeout=60.0)
    if response.status_code >= 400:
        sys.stderr.write(f"FAIL {path} {response.status_code}: {response.text[:500]}\n")
        response.raise_for_status()
    return response.json()


def _ingest_episode(episode: dict[str, Any]) -> dict[str, Any]:
    body = {
        "workspace_id": WORKSPACE,
        "task_id": TASK_ID,
        "source_type": "agent_action",
        "raw_text": f"[{episode['title']}] {episode['raw_text']}",
        "trust_level": "agent_observed",
        "importance": episode["importance"],
    }
    return _post("/memory/ingest_episode", body)


def _write_decision(decision: dict[str, Any]) -> dict[str, Any]:
    body = {
        "workspace_id": WORKSPACE,
        "title": decision["title"],
        "decision_text": decision["decision_text"],
        "rationale": decision["rationale"],
        "importance": decision["importance"],
    }
    return _post("/memory/write_decision", body)


def _update_task_state() -> dict[str, Any]:
    body = {
        "workspace_id": WORKSPACE,
        "task_id": TASK_ID,
        "goal": (
            "Audit the agent-memory-lite project end-to-end after the "
            "post-summary evolution and seed agentLight with durable findings."
        ),
        "status": "in_progress",
        "current_plan": [
            "Capture audit findings as agentLight episodes",
            "Record agentLight, isolation, and hook decisions",
            "Verify the dump round-trips through /memory/get_context",
        ],
        "completed_steps": [
            "Confirm HTTP service is healthy on the agent-memory-lite DB",
            "Confirm MCP env carries MEMORY_WORKSPACE_ID=agentLight and "
            "MEMORY_FORBID_DEFAULT_WORKSPACE=true",
            "Write 8 audit episodes and 3 decisions",
        ],
        "next_action": (
            "Verify with memory_get_context query=agentLight that the dump "
            "shows up in <retrieved_chunks> and <active_decisions>."
        ),
        "blockers": [],
        "files_in_scope": [
            ".claude/settings.json",
            "scripts/setup_agent.py",
            "scripts/inject_memory_context.py",
            "scripts/agentlight_audit_seed.py",
        ],
    }
    return _post("/memory/update_task_state", body)


def main() -> int:
    if not DB_PATH.exists():
        sys.stderr.write(
            f"agentLight DB not found at {DB_PATH}. Run scripts\\bootstrap_db.py first.\n"
        )
        return 2

    print("Seeding agentLight workspace via HTTP")
    print(f"  service : {BASE_URL}")
    print(f"  db      : {DB_PATH}")
    print(f"  vectors : {VEC_PATH}")
    print(f"  workspace_id: {WORKSPACE}")
    print()

    episode_results: list[dict[str, Any]] = []
    for episode in EPISODES:
        result = _ingest_episode(episode)
        print(
            f"  episode {result.get('episode_id', '?'):<24} "
            f"chunk {result.get('chunk_id', '?'):<24} "
            f"candidates={result.get('candidates_written', 0)} "
            f"-- {episode['title']}"
        )
        episode_results.append(result)

    print()
    decision_results: list[dict[str, Any]] = []
    for decision in DECISIONS:
        result = _write_decision(decision)
        print(f"  decision {result.get('decision_id', '?'):<24} -- {decision['title']}")
        decision_results.append(result)

    print()
    task_state = _update_task_state()
    print(f"  task_state task_id={task_state.get('task_id')}")
    print()

    summary = {
        "workspace_id": WORKSPACE,
        "db_path": str(DB_PATH),
        "vector_path": str(VEC_PATH),
        "episodes": episode_results,
        "decisions": decision_results,
        "task_state": task_state,
    }
    summary_path = REPO_ROOT / ".agent_memory" / "agentlight_audit_seed.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Wrote summary -> {summary_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
