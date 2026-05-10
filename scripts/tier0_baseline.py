"""Tier 0 cleanup baseline: capture metrics + classify candidates.

Read-only. Routes via X-Memory-DB-Path so hub-mode HTTP service lands
on the right physical DB per workspace. Outputs JSON summary the
operator can review before any destructive op.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

REGISTRY_PATH = Path.home() / ".agent_memory" / "workspaces.json"
SERVICE_BASE = "http://127.0.0.1:8765"
NOISE_KINDS = {"fix", "bug", "project_fact", "task_state", "relationship"}
AGE_DAYS = 7


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


def list_candidates(workspace_id: str, route: dict[str, str]) -> list[dict]:
    """Page through with limit=100 (server cap) until empty page."""
    out: list[dict] = []
    while True:
        r = httpx.post(
            f"{SERVICE_BASE}/memory/list_candidates",
            headers=headers_for(route),
            json={
                "workspace_id": workspace_id,
                "statuses": ["new"],
                "limit": 100,
                "until": out[-1].get("created_at") if out else None,
            },
            timeout=30,
        )
        r.raise_for_status()
        batch = r.json().get("candidates", [])
        # Filter dupes (since `until` is inclusive on equal timestamps).
        seen = {c["candidate_id"] for c in out}
        new_rows = [c for c in batch if c["candidate_id"] not in seen]
        if not new_rows:
            break
        out.extend(new_rows)
        if len(batch) < 100:
            break
    return out


def hygiene_report(workspace_id: str, route: dict[str, str]) -> dict:
    r = httpx.get(
        f"{SERVICE_BASE}/memory/hygiene_report",
        headers=headers_for(route),
        params={"workspace_id": workspace_id},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def main() -> int:
    registry = load_registry()
    targets = ["copyBot", "agentLight"]
    out: dict = {"workspaces": {}}
    for ws in targets:
        route = registry.get(ws)
        if not route:
            out["workspaces"][ws] = {"error": "not in registry"}
            continue
        cands = list_candidates(ws, route)
        cutoff = datetime.now(UTC) - timedelta(days=AGE_DAYS)
        kind_counts = Counter(c.get("kind", "?") for c in cands)
        old_noise: list[dict] = []
        for c in cands:
            kind = c.get("kind", "")
            if kind not in NOISE_KINDS:
                continue
            created_at = c.get("created_at", "") or c.get("temporal", {}).get("observed_at", "")
            if not created_at:
                continue
            try:
                ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts < cutoff:
                old_noise.append(
                    {
                        "id": c.get("candidate_id", "?"),
                        "kind": kind,
                        "subject": (c.get("subject") or "")[:80],
                        "trust_level": c.get("trust_level", "?"),
                        "confidence": c.get("confidence", 0.0),
                        "created_at": created_at,
                    }
                )
        try:
            hygiene = hygiene_report(ws, route)
            findings = hygiene.get("findings", [])
            hygiene_summary = {
                "status": hygiene.get("status", "?"),
                "findings_count": len(findings),
                "by_kind": Counter(f.get("kind", "?") for f in findings),
            }
        except httpx.HTTPError as exc:
            hygiene_summary = {"error": str(exc)}
        out["workspaces"][ws] = {
            "total_pending_candidates": len(cands),
            "kind_distribution": dict(kind_counts),
            "noise_candidates_age_gt_7d": {
                "count": len(old_noise),
                "ratio": round(len(old_noise) / max(1, len(cands)), 3),
                "sample_first_5": old_noise[:5],
            },
            "hygiene": hygiene_summary,
        }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
