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
        "/memory/list_theories",
        {
            "workspace_id": workspace_id,
            "query": query,
            "include_evidence": True,
            "evidence_limit": 2,
            "limit": limit,
        },
        timeout,
    )
    agenda = _post(
        base_url,
        "/memory/list_research_agenda",
        {"workspace_id": workspace_id, "query": query, "limit": limit},
        timeout,
    )
    context = _post(
        base_url,
        "/memory/get_context",
        {
            "workspace_id": workspace_id,
            "query": query or "research agenda theories experiments insights",
            "max_tokens": 2500,
        },
        timeout,
    )
    return {
        "workspace_id": workspace_id,
        "query": query,
        "theories": theories,
        "agenda": agenda,
        "context": context,
    }


def _line(label: str, value: object) -> str:
    return f"{label:<18} {value}"


def format_status(status: dict[str, Any]) -> str:
    theories = list(status["theories"].get("theories", []))
    agenda = dict(status["agenda"])
    snapshots = list(agenda.get("snapshots", []))
    experiments = list(agenda.get("experiments", []))
    insights = list(agenda.get("insights", []))
    concepts = list(agenda.get("concepts", []))
    context_text = str(status["context"].get("context_text", ""))

    lines = [
        "Research memory status",
        _line("workspace", status["workspace_id"]),
        _line("query", status.get("query") or "(default)"),
        _line("theories", len(theories)),
        _line("snapshots", len(snapshots)),
        _line("open_experiments", len(experiments)),
        _line("insights", len(insights)),
        _line("concepts", len(concepts)),
        _line("has_theories", "<active_theories>" in context_text),
        _line("has_agenda", "<research_agenda>" in context_text),
    ]

    if theories:
        lines.append("")
        lines.append("Top theories:")
        for item in theories[:5]:
            theory = item["theory"]
            lines.append(
                f"  - {theory['theory_id']} [{theory['status']}, {theory['confidence']:.2f}] "
                f"{theory['title']}"
            )
    if experiments:
        lines.append("")
        lines.append("Open experiments:")
        for experiment in experiments[:5]:
            lines.append(
                f"  - {experiment['experiment_id']} [{experiment['status']}, "
                f"priority={experiment['priority']:.2f}] {experiment['title']}"
            )
    if insights:
        lines.append("")
        lines.append("Insights:")
        for insight in insights[:5]:
            lines.append(
                f"  - {insight['insight_id']} [{insight['insight_type']}, "
                f"{insight['confidence']:.2f}] {insight['summary']}"
            )
    if snapshots:
        lines.append("")
        lines.append("Snapshots:")
        for snapshot in snapshots[:3]:
            lines.append(
                f"  - {snapshot['snapshot_id']} {snapshot['snapshot_key']} "
                f"rows={snapshot['total_rows']}"
            )
    return "\n".join(lines)


def _is_empty(status: dict[str, Any]) -> bool:
    return not status["theories"].get("theories") and not any(
        status["agenda"].get(key) for key in ("snapshots", "experiments", "insights", "concepts")
    )


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
