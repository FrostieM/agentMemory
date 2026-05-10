"""Tier 0 step 2: enumerate every remaining manual-review item per workspace.

Read-only. Pulls full details for theories_without_evidence, unlinked_insights,
remaining missing_capability_links (post-auto-triage), and new candidates so
the operator has one consolidated list with suggested actions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

REGISTRY_PATH = Path.home() / ".agent_memory" / "workspaces.json"
SERVICE_BASE = "http://127.0.0.1:8765"


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
    return h


def hygiene(workspace_id: str, route: dict[str, str]) -> dict:
    r = httpx.get(
        f"{SERVICE_BASE}/memory/hygiene_report",
        headers=headers_for(route),
        params={"workspace_id": workspace_id},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def candidates_new(workspace_id: str, route: dict[str, str]) -> list[dict]:
    r = httpx.post(
        f"{SERVICE_BASE}/memory/list_candidates",
        headers=headers_for(route),
        json={"workspace_id": workspace_id, "statuses": ["new"], "limit": 100},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("candidates", [])


def get_object(workspace_id: str, route: dict[str, str], kind: str, obj_id: str) -> dict | None:
    try:
        r = httpx.post(
            f"{SERVICE_BASE}/memory/get_object",
            headers=headers_for(route),
            json={"workspace_id": workspace_id, "kind": kind, "id": obj_id},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("object")
    except Exception:
        return None


def main() -> int:  # noqa: PLR0915 - linear ops script, per-section enumeration is the value
    registry = load_registry()
    targets = ["copyBot", "agentLight"]
    print()
    for ws in targets:
        route = registry.get(ws)
        if not route:
            continue
        print(f"### {ws}")
        print()
        try:
            h = hygiene(ws, route)
        except Exception as e:
            print(f"hygiene fetch failed: {e}")
            continue
        findings = h.get("findings", [])
        # group by kind
        by_kind: dict[str, list[dict]] = {}
        for f in findings:
            by_kind.setdefault(f.get("kind", "?"), []).append(f)
        # 1. theories without evidence
        for f in by_kind.get("theory_without_evidence", []):
            tid = f.get("target_id") or "?"
            obj = get_object(ws, route, "theory", tid)
            title = (obj or {}).get("title", "?")
            status = (obj or {}).get("status", "?")
            confidence = (obj or {}).get("confidence", "?")
            print(f"  [theory_without_evidence] {tid}")
            print(f"    title    : {title}")
            print(f"    status   : {status}, confidence: {confidence}")
            print(
                "    suggested: attach evidence via memory_add_theory_evidence"
                " OR archive if zombie hypothesis"
            )
            print()
        # 2. unlinked insights
        for f in by_kind.get("unlinked_insight", []):
            iid = f.get("target_id") or "?"
            obj = get_object(ws, route, "insight", iid)
            summary = (obj or {}).get("summary", "?")[:140]
            insight_type = (obj or {}).get("insight_type", "?")
            print(f"  [unlinked_insight] {iid}")
            print(f"    type     : {insight_type}")
            print(f"    summary  : {summary}")
            print(
                "    suggested: memory_update_insight target_type=theory|decision|skill"
                " + matching id"
            )
            print()
        # 3. remaining missing_capability_link (post auto-triage)
        for f in by_kind.get("missing_capability_link", []):
            tid = f.get("target_id") or "?"
            tt = f.get("target_type") or "?"
            obj = get_object(ws, route, tt, tid)
            title = (obj or {}).get("title", "?")
            print(f"  [missing_capability_link, post-auto-triage] {tt}/{tid}")
            print(f"    title    : {title}")
            print(
                "    suggested: manual memory_link_capability"
                " (auto-triage suggestions did not pass threshold)"
            )
            print()
        # 4. new candidates
        cands = candidates_new(ws, route)
        for c in cands:
            cid = c.get("candidate_id", "?")
            kind = c.get("kind", "?")
            subject = (c.get("subject") or "")[:120]
            print(f"  [candidate {kind}] {cid}")
            print(f"    subject : {subject}")
            print(f"    suggested: reject (kind={kind} not promotable)")
            print()
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
