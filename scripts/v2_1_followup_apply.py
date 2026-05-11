"""v2.1 follow-up: write the 'memory-first-before-source-edit'
behavior_instruction into both agentLight and copyBot, plus distill a
new insight about the three-independent-sources adoption-gap finding.

Idempotent. Routes via X-Memory-DB-Path. Each call reports outcome.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import httpx

with contextlib.suppress(AttributeError, ValueError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

REGISTRY_PATH = Path.home() / ".agent_memory" / "workspaces.json"
SERVICE_BASE = "http://127.0.0.1:8765"

BEHAVIOR_RULE = (
    "Before editing a source file in src/ (or equivalent project source root) "
    "that you have not read this session, call memory_file_digest(file_path=<path>) "
    "first. For non-trivial signature changes, also call "
    "memory_graph_neighbors(qualified_name=<symbol>, direction=upstream) to see "
    "every caller affected. Skip for trivial edits (single-line config tweak, "
    "dotfile, JSON literal). Grep stays the right tool for exact string substring "
    "search; memory tools are for symbol-level + graph-level questions Grep cannot "
    "answer (signature history, downstream callers, soft-edge similar functions, "
    "narrative file digest)."
)

BEHAVIOR_RATIONALE = (
    "Three independent AI agents (this assistant in 2026-05-10 sessions and a "
    "separate UVE-project agent in operator-shared evaluation) each defaulted to "
    "Grep + Read for code navigation despite having a fully-ingested code-memory "
    "substrate available. Root cause is training-data inertia, not system bug. "
    "Reinforcing the read-side discipline via a pinned workflow_preference is the "
    "cheapest non-system-change mitigation. Trigger is narrow (only fresh source "
    "files + signature-change edits) to avoid firing on every trivial edit -- "
    "63% of pre-existing behavior_instructions in copyBot never fire and that is "
    "the failure mode we are explicitly avoiding here."
)

INSIGHT_SUMMARY = (
    "Three independent AI agents working on agent-memory-lite (assistant in this "
    "2026-05-10 session), on copyBot (operator audit), and on a separate UVE TS "
    "project (operator-shared agent evaluation) each defaulted to Grep + Read for "
    "code navigation despite a fully-ingested code-memory substrate (UVE: 347 files / "
    "1419 symbols / 12836 edges; agentLight: 559 files / 1740 symbols / 12702 edges; "
    "copyBot: ingested). Each agent's honest self-reflection lists the same five "
    "reasons by descending weight: (1) training-data inertia toward Grep, (2) "
    "coverage uncertainty -- am I sure this file is ingested?, (3) latency calculation "
    "-- Grep is instant local, memory is HTTP roundtrip, (4) deferred-tool overhead -- "
    "memory MCP tools require a ToolSearch first-call, Grep is in the base toolset, "
    "(5) conservative bias -- risk-averse choice when Grep is guaranteed-correct on "
    "exact strings while memory ranking is probabilistic. This is now measurement, "
    "not theory. The four operator-validated system-level mitigations are: A) "
    "auto-inject relevant chunks into envelope when an edit is detected; B) "
    "memory_file_digest companion to Read in the agent runtime; C) a pinned "
    "workflow_preference behavior_instruction nudge before each fresh source file "
    "edit; D) a memory_status endpoint exposing ingest coverage so the agent stops "
    "guessing. C is shipped in this commit; D is in this commit too; A is a v2.1 "
    "envelope feature scheduled; B requires Claude Code upstream changes and is "
    "filed as an external request."
)


def load_registry() -> dict[str, dict[str, str]]:
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return {
        w["id"]: {"db_path": w.get("db_path", ""), "vector_path": w.get("vector_path", "")}
        for w in payload.get("workspaces", [])
    }


def headers_for(route: dict[str, str]) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if route.get("db_path"):
        h["X-Memory-DB-Path"] = route["db_path"]
    if route.get("vector_path"):
        h["X-Memory-Vector-Path"] = route["vector_path"]
    return h


def upsert_behavior(workspace_id: str, route: dict[str, str]) -> dict:
    body = {
        "workspace_id": workspace_id,
        "name": "memory-first-before-source-edit",
        "kind": "workflow_preference",
        "scope": "workspace",
        "priority": "user_preference",
        "rule": BEHAVIOR_RULE,
        "rationale": BEHAVIOR_RATIONALE,
        "applies_to": ["src/ edits", "signature-change refactors", "code navigation"],
        "conflict_policy": "current_user_wins",
        "source_type": "manual",
        "source_id": f"v2.1-followup-{workspace_id}-2026-05-11",
        "reviewed_by": "operator",
        "reviewed_at": "2026-05-11T00:00:00+00:00",
        "confidence": 0.95,
    }
    r = httpx.post(
        f"{SERVICE_BASE}/memory/upsert_behavior_instruction",
        headers=headers_for(route),
        json=body,
        timeout=30,
    )
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": r.text[:300]}
    return {"ok": True, **r.json()}


def pin_instruction(workspace_id: str, route: dict[str, str], instruction_id: str) -> dict:
    r = httpx.post(
        f"{SERVICE_BASE}/memory/pin",
        headers=headers_for(route),
        json={
            "workspace_id": workspace_id,
            "kind": "behavior_instruction",
            "id": instruction_id,
            "pinned": True,
        },
        timeout=30,
    )
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": r.text[:300]}
    return {"ok": True, **r.json()}


def distill_insight(workspace_id: str, route: dict[str, str]) -> dict:
    body = {
        "workspace_id": workspace_id,
        "insight_type": "lesson",
        "summary": INSIGHT_SUMMARY,
        "proposed_action": (
            "Ship the four mitigations in order of feasibility: C (behavior_instruction "
            "nudge) and D (memory_status endpoint) now; A (envelope auto-inject for "
            "edit-scope files) as v2.1; B (file_digest in Claude Code Read) as upstream "
            "request. Measure adoption delta by tracking memory_find_symbols + "
            "memory_file_digest + memory_graph_neighbors call counts in audit_log "
            "before/after deployment."
        ),
        "target_type": "decision",
        "target_id": "dec_ebc1c147bcde92e3",
        "confidence": 0.92,
        "tags": [
            "adoption-gap",
            "read-side-discipline",
            "code-memory",
            "v2.1-roadmap",
            "three-source-confirmation",
        ],
    }
    r = httpx.post(
        f"{SERVICE_BASE}/memory/distill_insight",
        headers=headers_for(route),
        json=body,
        timeout=30,
    )
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": r.text[:300]}
    return {"ok": True, **r.json()}


def main() -> int:
    registry = load_registry()
    al = registry["agentLight"]
    cb = registry["copyBot"]

    print("=== v2.1 follow-up apply ===\n")

    print("--- Behavior_instruction (pinned) ---")
    for ws, route in [("agentLight", al), ("copyBot", cb)]:
        out = upsert_behavior(ws, route)
        bid = out.get("instruction_id") or out.get("id")
        print(f"  {ws}: {out.get('ok')}, id={bid}")
        if out.get("ok") and bid:
            pin_out = pin_instruction(ws, route, bid)
            print(f"    pin: {pin_out.get('ok')}")
    print()

    print("--- Insight (agentLight, three-source confirmation) ---")
    out = distill_insight("agentLight", al)
    iid = out.get("insight_id")
    print(f"  agentLight: ok={out.get('ok')}, id={iid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
