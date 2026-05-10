"""Tier 0 step 2 application: bulk reject + archive smokes + link insights.

Three idempotent batches against the running HTTP service. Every call
routes via X-Memory-DB-Path. Each batch reports counts before/after
so the operator sees exactly what changed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

REGISTRY_PATH = Path.home() / ".agent_memory" / "workspaces.json"
SERVICE_BASE = "http://127.0.0.1:8765"

# Batch A: reject 9 noise candidates in copyBot.
COPYBOT_REJECTS = [
    "cand_ab7683d769e0305e",
    "cand_d1b994429288693e",
    "cand_4ff4a93b076049d0",
    "cand_351230c9c9e2a721",
    "cand_183f2df3de6e2f5e",
    "cand_3ce4b5bdc903eb36",
    "cand_a8b241a561131a14",
    "cand_067abf76b297b2aa",
    "cand_7615746ed6d3c37a",
]

# Batch B: archive 3 ephemeral smoke-test theories on agentLight.
AGENTLIGHT_THEORY_ARCHIVES = [
    "th_38c83da5fa9e2455",
    "th_b95f78bf3eeb66ae",
    "th_85de8ad168e06e2d",
]

# Batch C: link 7 unlinked insights on agentLight to best-fit existing target.
# Mapping rationale: insights about memory discipline / adoption / audit
# pattern point at the existing v2 consolidation decision OR the
# Memory-population-discipline skill that owns that operating space.
# Tighter targeting (per-insight new playbook) is operator follow-up.
AGENTLIGHT_INSIGHT_LINKS = [
    # empty-envelope hook investigation -> Memory population discipline skill
    ("insight_7936ea8017d8533d", "skill", "skill_81b73a2284f20e6a"),
    # code-memory adoption gap -> v2 consolidation decision
    ("insight_899fcc9976fbd82b", "decision", "dec_ebc1c147bcde92e3"),
    # 6-round adversarial audit pattern -> Memory population discipline skill
    ("insight_587b858228109e40", "skill", "skill_81b73a2284f20e6a"),
    # negative results from tuning sweeps -> Memory population discipline skill
    ("insight_ef45328d62fff916", "skill", "skill_81b73a2284f20e6a"),
    # HTTP/MCP parity chokepoint -> v1.10 release decision (where parity was locked)
    ("insight_8625e562ae7f5968", "decision", "dec_09505558584767d3"),
    # real bug vs misdiagnosis -> Memory population discipline skill
    ("insight_f12f00836fa9d169", "skill", "skill_81b73a2284f20e6a"),
    # calibration before claim -> v2 consolidation decision
    ("insight_42841926a9c165df", "decision", "dec_ebc1c147bcde92e3"),
]


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


def hygiene_total(workspace_id: str, route: dict[str, str]) -> int:
    r = httpx.get(
        f"{SERVICE_BASE}/memory/hygiene_report",
        headers=headers_for(route),
        params={"workspace_id": workspace_id},
        timeout=30,
    )
    r.raise_for_status()
    return len(r.json().get("findings", []))


def reject_one(workspace_id: str, route: dict[str, str], cid: str) -> dict:
    r = httpx.post(
        f"{SERVICE_BASE}/memory/reject_candidate",
        headers=headers_for(route),
        json={"candidate_id": cid},
        timeout=30,
    )
    if r.status_code == 200:
        return {"id": cid, "ok": True}
    return {"id": cid, "ok": False, "status": r.status_code, "detail": r.text[:200]}


def archive_one(workspace_id: str, route: dict[str, str], kind: str, obj_id: str) -> dict:
    r = httpx.post(
        f"{SERVICE_BASE}/memory/archive",
        headers=headers_for(route),
        json={"workspace_id": workspace_id, "kind": kind, "id": obj_id, "archive": True},
        timeout=30,
    )
    if r.status_code == 200:
        return {"id": obj_id, "ok": True}
    return {"id": obj_id, "ok": False, "status": r.status_code, "detail": r.text[:200]}


def link_insight(
    workspace_id: str, route: dict[str, str], iid: str, target_type: str, target_id: str
) -> dict:
    r = httpx.post(
        f"{SERVICE_BASE}/memory/update_insight",
        headers=headers_for(route),
        json={
            "workspace_id": workspace_id,
            "insight_id": iid,
            "target_type": target_type,
            "target_id": target_id,
            "status": "accepted",
        },
        timeout=30,
    )
    if r.status_code == 200:
        return {"id": iid, "ok": True, "target": f"{target_type}/{target_id}"}
    return {"id": iid, "ok": False, "status": r.status_code, "detail": r.text[:200]}


def main() -> int:
    registry = load_registry()
    cb = registry["copyBot"]
    al = registry["agentLight"]

    print("=== Tier 0 step 2 apply ===")
    print()
    print(f"copyBot hygiene_findings BEFORE: {hygiene_total('copyBot', cb)}")
    print(f"agentLight hygiene_findings BEFORE: {hygiene_total('agentLight', al)}")
    print()

    print("--- Batch A: reject 9 copyBot candidates ---")
    for cid in COPYBOT_REJECTS:
        out = reject_one("copyBot", cb, cid)
        print(f"  {out}")

    print()
    print("--- Batch B: archive 3 agentLight smoke theories ---")
    for tid in AGENTLIGHT_THEORY_ARCHIVES:
        out = archive_one("agentLight", al, "theory", tid)
        print(f"  {out}")

    print()
    print("--- Batch C: link 7 agentLight insights ---")
    for iid, ttype, tid in AGENTLIGHT_INSIGHT_LINKS:
        out = link_insight("agentLight", al, iid, ttype, tid)
        print(f"  {out}")

    print()
    print(f"copyBot hygiene_findings AFTER : {hygiene_total('copyBot', cb)}")
    print(f"agentLight hygiene_findings AFTER : {hygiene_total('agentLight', al)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
