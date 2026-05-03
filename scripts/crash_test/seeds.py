"""Reusable seed helpers shared across phases.

Each helper writes a small, predictable amount of test data and returns
the IDs / records the calling phase needs for assertions. Keeping seeds
out of phase modules avoids duplicate fixture code as the test grows.
"""

from __future__ import annotations

from typing import Any

import httpx


def post(client: httpx.Client, path: str, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post(path, json=body, timeout=30.0)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {"response": data}


def get(client: httpx.Client, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.get(path, params=params, timeout=30.0)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {"response": data}


_EPISODE_BODIES = (
    # Three distinct topics so episode_dedup (default ON in 1.1.0) does not
    # collapse them into a single chunk. Kept short and topical so FTS keeps
    # working on individual keywords downstream phases search for.
    "Investigated retrieval RRF fusion with FTS bm25 plus vector cosine; "
    "rrf_norm gives multi-source presence boost on top of base scoring.",
    "Audited LanceDB workspace namespace lifecycle: empty namespace count, "
    "upsert, delete, drop. Vector dim verified at 384 for e5-small.",
    "Verified extraction trust gate blocks document-sourced candidates "
    "from promotion to core memory or procedural rules. Sentinel YAML "
    "passes on golden corpus.",
    "Hygiene report flags stale candidates and weak theories. Suggested "
    "capability links surface for review without auto-promotion.",
    "Compaction watchdog probes chunk count vs threshold and emits a "
    "compaction_due maintenance event on overdue workspaces.",
)


def seed_episodes(client: httpx.Client, *, workspace_id: str, count: int = 3) -> list[str]:
    ids: list[str] = []
    for i in range(count):
        body = _EPISODE_BODIES[i % len(_EPISODE_BODIES)]
        out = post(
            client,
            "/memory/ingest_episode",
            {
                "workspace_id": workspace_id,
                "session_id": f"sess_{i}",
                "task_id": f"task_{i}",
                "source_type": "agent_action",
                "raw_text": f"Iteration {i} for workspace {workspace_id}: {body}",
                "trust_level": "agent_observed",
                "importance": 0.5,
            },
        )
        ids.append(str(out["episode_id"]))
    return ids


def seed_decisions(client: httpx.Client, *, workspace_id: str) -> list[str]:
    """Two decisions where the second supersedes the first."""
    first = post(
        client,
        "/memory/write_decision",
        {
            "workspace_id": workspace_id,
            "title": "Use SQLite WAL for source-of-record",
            "decision_text": (
                "All durable memory rows live in a single SQLite database operating "
                "in WAL mode for concurrent reads."
            ),
            "rationale": "Local-only deployment with embedded backups.",
        },
    )
    second = post(
        client,
        "/memory/write_decision",
        {
            "workspace_id": workspace_id,
            "title": "Adopt LanceDB for vector store",
            "decision_text": "LanceDB is the default vector backend; sqlite-vec stays opt-in.",
            "rationale": "LanceDB ships pre-built wheels and supports per-workspace namespaces.",
            "supersedes_decision_id": str(first["decision_id"]),
        },
    )
    return [str(first["decision_id"]), str(second["decision_id"])]


def seed_theories(client: httpx.Client, *, workspace_id: str) -> list[str]:
    ids: list[str] = []
    for status in ("testing", "validated", "rejected"):
        out = post(
            client,
            "/memory/write_theory",
            {
                "workspace_id": workspace_id,
                "title": f"Test theory ({status})",
                "domain": "qa",
                "claim": f"In {status} state we expect retrieval to honor status filtering.",
                "predictions": [f"{status} flag changes context envelope shape"],
                "validation_criteria": ["≥1 evidence row collected"],
                "status": status,
                "confidence": 0.55,
                "importance": 0.6,
            },
        )
        ids.append(str(out["theory_id"]))
    return ids


def seed_capabilities(client: httpx.Client, *, workspace_id: str) -> dict[str, str]:
    role = post(
        client,
        "/memory/upsert_agent_role",
        {
            "workspace_id": workspace_id,
            "name": "QA crash-test operator",
            "purpose": "Validate that every memory feature behaves as documented.",
            "responsibilities": ["Run all phases", "Capture failures with evidence"],
            "boundaries": ["Never touch the production workspace"],
            "tools": ["/memory/get_context", "/memory/search"],
        },
    )
    skill = post(
        client,
        "/memory/upsert_agent_skill",
        {
            "workspace_id": workspace_id,
            "name": "End-to-end memory verification",
            "summary": "Hit every endpoint, assert invariants, report failures.",
            "when_to_use": ["After a release", "When a feature flag changes"],
            "inputs": ["Workspace_id", "List of phases to run"],
            "outputs": ["Pass/fail report", "Audit trail evidence"],
            "tools": ["HTTP client", "SQLite read access"],
            "related_roles": ["QA crash-test operator"],
        },
    )
    playbook = post(
        client,
        "/memory/upsert_agent_playbook",
        {
            "workspace_id": workspace_id,
            "name": "Pre-deploy crash test",
            "goal": "Catch regressions before pushing",
            "triggers": ["About to push to origin/main"],
            "steps": ["Bootstrap fresh workspace", "Run all phases", "Inspect failures"],
            "success_criteria": ["No FAIL phases", "All trust-gate invariants intact"],
            "required_skills": ["End-to-end memory verification"],
        },
    )
    return {
        "role_id": str(role["role_id"]),
        "skill_id": str(skill["skill_id"]),
        "playbook_id": str(playbook["playbook_id"]),
    }


def seed_behavior_instruction(client: httpx.Client, *, workspace_id: str) -> str:
    out = post(
        client,
        "/memory/upsert_behavior_instruction",
        {
            "workspace_id": workspace_id,
            "name": "Crash-test reporting style",
            "kind": "communication_style",
            "scope": "workspace",
            "priority": "user_preference",
            "rule": "Report assertion failures with phase, target id, and observed value.",
            "rationale": "Operators need actionable evidence not generic status.",
            "applies_to": ["crash test reports"],
            "conflict_policy": "current_user_wins",
            "source_type": "manual",
            "confidence": 0.95,
        },
    )
    return str(out["instruction_id"])
