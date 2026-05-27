"""Print a concise research-memory status report from a running service.

Examples:
    python scripts/research_status.py --workspace default
    python scripts/research_status.py --workspace default --query "paper selector"
    python scripts/research_status.py --workspace default --json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx


def _post(base_url: str, path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    response = httpx.post(f"{base_url.rstrip('/')}{path}", json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError(f"{path} returned non-object JSON")
    return data


def _get(base_url: str, path: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    response = httpx.get(f"{base_url.rstrip('/')}{path}", params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError(f"{path} returned non-object JSON")
    return data


def fetch_status(
    *,
    base_url: str,
    workspace_id: str,
    query: str | None,
    limit: int,
    timeout: float,
) -> dict[str, Any]:
    theories = _post(
        base_url,
        "/memory/search",
        {
            "workspace_id": workspace_id,
            "query": query or "research theories",
            "kinds": ["theory"],
            "limit": limit,
        },
        timeout,
    )
    research = _post(
        base_url,
        "/memory/search",
        {
            "workspace_id": workspace_id,
            "query": query or "research agenda theories experiments insights snapshots concepts",
            "kinds": ["theory", "insight", "concept", "snapshot"],
            "limit": limit,
        },
        timeout,
    )
    context = _get(
        base_url,
        "/memory/brief",
        {
            "workspace_id": workspace_id,
            "task": query or "research agenda theories experiments insights",
            "max_tokens": 2000,
        },
        timeout,
    )
    return {
        "workspace_id": workspace_id,
        "query": query,
        "theories": theories,
        "research": research,
        "context": context,
    }


def _line(label: str, value: object) -> str:
    return f"{label:<18} {value}"


def format_status(status: dict[str, Any]) -> str:
    theory_hits = list(status["theories"].get("data", []))
    theories = [
        hit.get("projection", {})
        for hit in theory_hits
        if isinstance(hit, dict) and isinstance(hit.get("projection"), dict)
    ]
    research_hits = list(status["research"].get("data", []))
    by_kind: dict[str, list[dict[str, Any]]] = {
        "snapshot": [],
        "insight": [],
        "concept": [],
    }
    for hit in research_hits:
        if not isinstance(hit, dict):
            continue
        projection = hit.get("projection")
        if isinstance(projection, dict):
            by_kind.setdefault(str(hit.get("kind")), []).append(projection)
    snapshots = by_kind.get("snapshot", [])
    insights = by_kind.get("insight", [])
    concepts = by_kind.get("concept", [])
    lines = [
        "Research memory status",
        _line("workspace", status["workspace_id"]),
        _line("query", status.get("query") or "(default)"),
        _line("theories", len(theories)),
        _line("snapshots", len(snapshots)),
        _line("insights", len(insights)),
        _line("concepts", len(concepts)),
        _line("has_theories", bool(theories)),
        _line("has_research", bool(research_hits)),
    ]

    if theories:
        lines.append("")
        lines.append("Top theories:")
        for item in theories[:5]:
            lines.append(
                f"  - {item['id']} [{item.get('status')}, {item.get('confidence', 0.0):.2f}] "
                f"{item.get('title')}"
            )
    if insights:
        lines.append("")
        lines.append("Insights:")
        for insight in insights[:5]:
            lines.append(
                f"  - {insight['id']} [{insight.get('insight_type')}] {insight.get('gist')}"
            )
    if snapshots:
        lines.append("")
        lines.append("Snapshots:")
        for snapshot in snapshots[:3]:
            lines.append(
                f"  - {snapshot['id']} {snapshot.get('snapshot_key')} "
                f"rows={snapshot.get('total_rows')}"
            )
    return "\n".join(lines)


def _is_empty(status: dict[str, Any]) -> bool:
    return not status["theories"].get("data") and not status["research"].get("data")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show research-memory status.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--workspace", default="default")
    parser.add_argument("--query", default=None)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json", action="store_true", help="Print raw JSON.")
    parser.add_argument(
        "--fail-on-empty",
        action="store_true",
        help="Return exit code 1 if no research memory is found.",
    )
    args = parser.parse_args(argv)

    try:
        status = fetch_status(
            base_url=args.base_url,
            workspace_id=args.workspace,
            query=args.query,
            limit=args.limit,
            timeout=args.timeout,
        )
    except (httpx.HTTPError, ValueError) as exc:
        print(f"research_status_error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        print(format_status(status))
    if args.fail_on_empty and _is_empty(status):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
