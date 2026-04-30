"""Render a concise Markdown operator report from memory_trust_dashboard."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a memory operator report.")
    parser.add_argument("--workspace", "--workspace-id", dest="workspace", default=None)
    parser.add_argument("--db-path", "--db", dest="db_path", default=None)
    parser.add_argument("--vector-path", "--vectors", dest="vector_path", default=None)
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--sentinels", default=None)
    parser.add_argument("--require-sentinels", action="store_true")
    parser.add_argument("--output", default=None, help="Optional Markdown output path.")
    return parser


def _script(name: str) -> str:
    return str(Path(__file__).with_name(name))


def _dashboard_payload(args: argparse.Namespace) -> dict[str, Any]:
    cmd = [sys.executable, _script("memory_trust_dashboard.py"), "--json"]
    if args.workspace:
        cmd.extend(["--workspace", args.workspace])
    if args.db_path:
        cmd.extend(["--db-path", args.db_path])
    if args.vector_path:
        cmd.extend(["--vector-path", args.vector_path])
    if args.project_root:
        cmd.extend(["--project-root", args.project_root])
    if args.sentinels:
        cmd.extend(["--sentinels", args.sentinels])
    if args.require_sentinels:
        cmd.append("--require-sentinels")

    completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
    try:
        payload = cast(dict[str, Any], json.loads(completed.stdout))
    except json.JSONDecodeError:
        return {
            "status": "degraded",
            "workspace_id": args.workspace or "<unknown>",
            "failures": ["memory_trust_dashboard did not emit JSON"],
            "warnings": [],
            "components": {},
            "stderr": completed.stderr.strip(),
        }
    if completed.returncode not in {0, 2}:
        payload.setdefault("failures", []).append(
            f"memory_trust_dashboard exited {completed.returncode}"
        )
    if completed.stderr.strip():
        payload.setdefault("warnings", []).append(completed.stderr.strip())
    return payload


def _component_table(components: dict[str, Any]) -> list[str]:
    lines = ["| Component | Status | Exit |", "| --- | --- | --- |"]
    for name, component in sorted(components.items()):
        if not isinstance(component, dict):
            continue
        lines.append(
            f"| {name} | {component.get('status', 'unknown')} | {component.get('exit_code', '-')} |"
        )
    return lines


def _integrity_counts(components: dict[str, Any]) -> dict[str, Any]:
    integrity = components.get("integrity")
    if not isinstance(integrity, dict):
        return {}
    counts = integrity.get("counts")
    return counts if isinstance(counts, dict) else {}


def _feedback_counts(components: dict[str, Any]) -> dict[str, Any]:
    feedback = components.get("feedback")
    if not isinstance(feedback, dict):
        return {}
    counts = feedback.get("counts")
    return counts if isinstance(counts, dict) else {}


def render_markdown(payload: dict[str, Any]) -> str:
    components = payload.get("components")
    components = components if isinstance(components, dict) else {}
    integrity_counts = _integrity_counts(components)
    feedback_counts = _feedback_counts(components)
    failures = [str(item) for item in payload.get("failures", [])]
    warnings = [str(item) for item in payload.get("warnings", [])]

    lines = [
        f"# Memory Operator Report: {payload.get('workspace_id', '<unknown>')}",
        "",
        f"- status: `{payload.get('status', 'unknown')}`",
        f"- db: `{payload.get('db_path', '-')}`",
        f"- vectors: `{payload.get('vector_path', '-')}`",
        "",
        "## Key Counts",
        "",
        f"- chunks / fts / vectors: `{integrity_counts.get('chunks', '-')}` / "
        f"`{integrity_counts.get('chunks_fts', '-')}` / `{integrity_counts.get('vectors', '-')}`",
        f"- missing embedding ids: `{integrity_counts.get('missing_embedding_ids', '-')}`",
        f"- open maintenance events: `{integrity_counts.get('open_maintenance_events', '-')}`",
        f"- hygiene findings: `{integrity_counts.get('hygiene_findings', '-')}`",
        f"- capability links: `{integrity_counts.get('capability_links', '-')}`",
        f"- feedback total / noisy sources: `{feedback_counts.get('total', '-')}` / "
        f"`{feedback_counts.get('noisy_sources', '-')}`",
        "",
        "## Components",
        "",
        *_component_table(components),
        "",
        "## Failures",
        "",
    ]
    lines.extend([f"- {item}" for item in failures] if failures else ["- none"])
    lines.extend(["", "## Warnings", ""])
    lines.extend([f"- {item}" for item in warnings] if warnings else ["- none"])
    lines.extend(
        [
            "",
            "## Operator Decision",
            "",
            "- If status is `ok`, memory is trustworthy for normal agent work.",
            "- If status is `warning`, review warnings before relying on research conclusions.",
            "- If status is `degraded`, run the named component directly and repair only with backup-first tooling.",
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = _dashboard_payload(args)
    markdown = render_markdown(payload)
    if args.output:
        Path(args.output).write_text(markdown, encoding="utf-8")
    else:
        print(markdown, end="")
    return 2 if payload.get("status") == "degraded" else 0


if __name__ == "__main__":
    raise SystemExit(main())
