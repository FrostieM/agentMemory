"""Reusable seed helpers shared across crash-test phases."""

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
    "Investigated retrieval RRF fusion with FTS bm25 plus vector cosine; "
    "rrf_norm gives multi-source presence boost on top of base scoring.",
    "Audited LanceDB workspace namespace lifecycle: empty namespace count, "
    "upsert, delete, drop. Vector dim verified at 384 for e5-small.",
    "Verified extraction trust gate blocks document-sourced candidates "
    "from automatic promotion. Sentinel YAML passes on golden corpus.",
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
            "/memory/write",
            {
                "workspace_id": workspace_id,
                "kind": "episode",
                "payload": {
                    "session_id": f"sess_{i}",
                    "task_id": f"task_{i}",
                    "source_type": "agent_action",
                    "raw_text": f"Iteration {i} for workspace {workspace_id}: {body}",
                    "trust_level": "agent_observed",
                    "importance": 0.5,
                },
            },
        )
        ids.append(str(out["data"]["episode_id"]))
    return ids


def seed_decisions(client: httpx.Client, *, workspace_id: str) -> list[str]:
    """Two decisions where the second supersedes the first."""
    first = post(
        client,
        "/memory/write",
        {
            "workspace_id": workspace_id,
            "kind": "decision",
            "payload": {
                "title": "Use SQLite WAL for source-of-record",
                "decision_text": (
                    "All durable memory rows live in a single SQLite database operating "
                    "in WAL mode for concurrent reads."
                ),
                "rationale": "Local-only deployment with embedded backups.",
            },
        },
    )["data"]
    second = post(
        client,
        "/memory/write",
        {
            "workspace_id": workspace_id,
            "kind": "decision",
            "payload": {
                "title": "Adopt LanceDB for vector store",
                "decision_text": "LanceDB is the default vector backend; sqlite-vec stays opt-in.",
                "rationale": "LanceDB ships pre-built wheels and supports per-workspace namespaces.",
                "supersedes_decision_id": str(first["decision_id"]),
            },
        },
    )["data"]
    return [str(first["decision_id"]), str(second["decision_id"])]


def seed_theories(client: httpx.Client, *, workspace_id: str) -> list[str]:
    ids: list[str] = []
    for status in ("testing", "validated", "rejected"):
        out = post(
            client,
            "/memory/write",
            {
                "workspace_id": workspace_id,
                "kind": "theory",
                "payload": {
                    "title": f"Test theory ({status})",
                    "domain": "qa",
                    "claim": f"In {status} state we expect retrieval to honor status filtering.",
                    "predictions": [f"{status} flag changes brief/search retrieval shape"],
                    "validation_criteria": [">=1 evidence row collected"],
                    "status": status,
                    "confidence": 0.55,
                    "importance": 0.6,
                },
            },
        )
        ids.append(str(out["data"]["theory_id"]))
    return ids


def seed_capabilities(client: httpx.Client, *, workspace_id: str) -> dict[str, str]:
    skill = post(
        client,
        "/memory/write",
        {
            "workspace_id": workspace_id,
            "kind": "skill",
            "payload": {
                "name": "End-to-end memory verification",
                "body_md": (
                    "Use HTTP client and SQLite read access to hit canonical v3 "
                    "memory endpoints, assert invariants, and report failures."
                ),
                "summary": "Hit canonical v3 endpoints, assert invariants, report failures.",
                "trigger": "After a release or when a feature flag changes.",
                "active": True,
                "confidence": 0.85,
            },
        },
    )
    skill_data = skill.get("data") if isinstance(skill.get("data"), dict) else {}
    skill_id = str(skill_data.get("id") or skill_data.get("skill_id") or "")
    return {
        "role_id": skill_id,
        "skill_id": skill_id,
        "playbook_id": skill_id,
    }


def seed_behavior_instruction(client: httpx.Client, *, workspace_id: str) -> str:
    out = post(
        client,
        "/memory/write",
        {
            "workspace_id": workspace_id,
            "kind": "behavior",
            "payload": {
                "name": "Crash-test reporting style",
                "kind": "communication_style",
                "scope": "workspace",
                "priority": "user_preference",
                "rule": "Report assertion failures with phase, target id, and observed value.",
                "rationale": "Operators need actionable evidence, not generic status.",
                "applies_to": ["crash test reports"],
                "conflict_policy": "current_user_wins",
                "source_type": "manual",
                "confidence": 0.95,
            },
        },
    )["data"]
    return str(out["instruction_id"])
