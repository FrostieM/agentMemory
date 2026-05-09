#!/usr/bin/env python3
"""One-screen memory status overview (1.3.0).

Hits a handful of read-only endpoints and prints a single-page summary:
service version, workspace, hygiene + quality_gate counts, telemetry
ratio + per-agent split, recent activity. Replaces 5 manual curl calls.

Usage:
    python scripts/memory_status.py --workspace copyBot
    python scripts/memory_status.py --workspace copyBot --base-url http://127.0.0.1:8765 \\
        --db-path C:/Users/.../copyBot/.agent_memory/memory.db
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx


def _get(client: httpx.Client, base_url: str, path: str, **params: Any) -> dict[str, Any] | None:
    try:
        r = client.get(f"{base_url}{path}", params=params, timeout=5.0)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as exc:
        return {"error": str(exc)}


def main() -> int:  # noqa: PLR0915 — single CLI main, naturally long
    parser = argparse.ArgumentParser(description="One-screen memory status overview")
    parser.add_argument("--workspace", "--workspace-id", required=True, dest="workspace_id")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--db-path", default=None, help="X-Memory-DB-Path header for hub mode")
    parser.add_argument("--vector-path", default=None, help="X-Memory-Vector-Path header")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()

    headers: dict[str, str] = {}
    if args.db_path:
        headers["X-Memory-DB-Path"] = args.db_path
    if args.vector_path:
        headers["X-Memory-Vector-Path"] = args.vector_path

    with httpx.Client(headers=headers) as client:
        health = _get(client, args.base_url, "/health") or {}
        hygiene = (
            _get(client, args.base_url, "/memory/hygiene_report", workspace_id=args.workspace_id)
            or {}
        )
        quality = (
            _get(client, args.base_url, "/memory/quality_gate", workspace_id=args.workspace_id)
            or {}
        )
        telemetry = (
            _get(
                client,
                args.base_url,
                "/memory/telemetry",
                workspace_id=args.workspace_id,
                days=args.days,
            )
            or {}
        )
        cold = (
            _get(
                client,
                args.base_url,
                "/memory/cold_decisions",
                workspace_id=args.workspace_id,
                cutoff_days=30,
                limit=5,
            )
            or {}
        )

    if args.json:
        print(
            json.dumps(
                {
                    "health": health,
                    "hygiene": hygiene,
                    "quality_gate": quality,
                    "telemetry": telemetry,
                    "cold_decisions": cold,
                },
                indent=2,
            )
        )
        return 0

    # Plain-text summary.
    print(f"=== memory_status — workspace={args.workspace_id} ===\n")

    # Service version + integrity
    version = health.get("version", "?")
    status = health.get("status", "?")
    ws = health.get("workspace_id", "?")
    print(f"Service:       v{version}  status={status}  current_workspace={ws}")

    # Hygiene + quality
    h_status = hygiene.get("status", "?")
    h_count = len(hygiene.get("findings", []))
    q_status = quality.get("status", "?")
    q_findings = quality.get("findings", [])
    q_errors = sum(1 for f in q_findings if f.get("severity") == "error")
    q_warns = sum(1 for f in q_findings if f.get("severity") == "warning")
    print(f"Hygiene:       {h_status}  ({h_count} findings)")
    print(
        f"Quality gate:  {q_status}  ({len(q_findings)} findings: {q_errors} error, {q_warns} warn)"
    )

    # Telemetry
    s_total = telemetry.get("search_total", 0)
    w_total = telemetry.get("write_total", 0)
    ratio = telemetry.get("search_per_write_ratio", 0.0)
    hit_rate = telemetry.get("search_hit_rate", 0.0)
    print(f"\nTelemetry (last {args.days}d):")
    print(f"  search:     {s_total}")
    print(f"  write:      {w_total}")
    print(f"  ratio:      {ratio}  (target: 0.5-1.5)")
    print(f"  hit-rate:   {hit_rate}  (search calls returning >=1 hit)")

    # Per-agent split
    by_agent = telemetry.get("by_agent") or []
    if by_agent:
        print("\nPer-agent split:")
        for row in by_agent[:6]:
            print(
                f"  {row['agent_id']:20s}  search={row['search']:5d}  write={row['write']:5d}  ratio={row['ratio']}"
            )

    # Cold decisions
    cold_count = cold.get("cold_count", 0)
    total_active = cold.get("total_active", 0)
    print(f"\nCold decisions (>30d untouched): {cold_count}/{total_active}")
    for r in cold.get("rows", [])[:3]:
        days = r.get("days_cold", "?")
        title = (r.get("title") or "")[:50]
        pinned = " [pinned]" if r.get("pinned") else ""
        print(f"  {days:>4}d  {title}{pinned}")

    # Action hint
    print()
    if h_count > 50 or q_errors > 10:
        print("HINT: hygiene/quality gate has notable debt. Consider:")
        print(
            "  python scripts/memory_auto_triage.py --workspace "
            + args.workspace_id
            + " --apply --backup-first"
        )
    if ratio < 0.3 and (s_total + w_total) > 30:
        print("HINT: search rate is low. Agent may be missing the search-discipline BI.")
        print('  Verify <behavior_instructions> envelope contains "Search before write".')

    return 0


if __name__ == "__main__":
    sys.exit(main())
