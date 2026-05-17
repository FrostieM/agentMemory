"""Measure agent tool-usage discipline.

Parses Claude Code session transcripts (``~/.claude/projects/*/*.jsonl``)
and tallies how often the agent reaches for cheap-but-blind tools
(Read, Grep, Glob) vs the v3 graph primitives
(memory_v3_impact_check, memory_v3_get, memory_v3_search,
memory_graph_neighbors, memory_find_symbols).

The number that matters:  **graph_share = graph_calls / (graph_calls
+ read_grep_calls)**.  Plan target ≥ 0.30 after the discipline stack
is live — anything lower means the rules don't fire often enough or
the agent isn't paying attention to the brief.

Usage::

    python scripts/measure_tool_usage.py
    python scripts/measure_tool_usage.py --since-days 7
    python scripts/measure_tool_usage.py --project agent-memory-lite --json
    python scripts/measure_tool_usage.py --transcript path/to/specific.jsonl

Output:  per-project + cross-project rollup with the discipline ratio
and a verdict (poor / fair / good / strong).
"""

from __future__ import annotations

import argparse
import calendar
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_TRANSCRIPT_ROOT = Path.home() / ".claude" / "projects"

# Tool buckets — adjust here when adding new v3 primitives or v2 graph tools.
READ_GREP_TOOLS = frozenset({"Read", "Grep", "Glob"})
GRAPH_TOOLS = frozenset(
    {
        "memory_v3_impact_check",
        "memory_v3_get",
        "memory_v3_search",
        "memory_v3_brief",
        "memory_graph_neighbors",
        "memory_find_symbols",
        "memory_file_digest",
        "memory_code_overview",
        "memory_code_graph",
        "memory_breaking_changes",
        "memory_symbol_history",
    }
)
EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})

# Verdict thresholds on graph_share = graph / (graph + read_grep).
_VERDICTS = (
    (0.50, "strong"),  # discipline solid; v3 primitives are first instinct
    (0.30, "good"),  # plan target; agent reaches for graph reliably
    (0.15, "fair"),  # discipline lands sometimes; brief / lint may help
    (0.00, "poor"),  # graph tools effectively unused
)


# ============================================================
# Data model
# ============================================================


@dataclass(slots=True)
class ProjectStats:
    """Per-project tally."""

    project: str
    transcript_count: int = 0
    tool_counts: Counter[str] = field(default_factory=Counter)

    @property
    def read_grep_total(self) -> int:
        return sum(self.tool_counts[t] for t in READ_GREP_TOOLS)

    @property
    def graph_total(self) -> int:
        return sum(self.tool_counts[t] for t in GRAPH_TOOLS)

    @property
    def edit_total(self) -> int:
        return sum(self.tool_counts[t] for t in EDIT_TOOLS)

    @property
    def graph_share(self) -> float:
        denom = self.graph_total + self.read_grep_total
        return self.graph_total / denom if denom else 0.0

    def verdict(self) -> str:
        share = self.graph_share
        for threshold, label in _VERDICTS:
            if share >= threshold:
                return label
        return "poor"

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "transcript_count": self.transcript_count,
            "read_grep_total": self.read_grep_total,
            "graph_total": self.graph_total,
            "edit_total": self.edit_total,
            "graph_share": round(self.graph_share, 3),
            "verdict": self.verdict(),
            "top_tools": dict(self.tool_counts.most_common(10)),
        }


# ============================================================
# Transcript parsing
# ============================================================


def _iter_tool_uses(transcript_path: Path) -> Any:
    """Yield each tool_use block from one JSONL transcript."""
    try:
        f = transcript_path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message") if isinstance(obj, dict) else None
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    yield item


def _parse_ts(timestamp: str) -> float | None:
    """Return epoch seconds for ISO-ish timestamps, or None on parse fail."""
    if not timestamp:
        return None
    try:
        # Claude Code uses ISO 8601 with Z suffix.
        clean = timestamp.replace("Z", "")
        return calendar.timegm(time.strptime(clean, "%Y-%m-%dT%H:%M:%S.%f"))
    except ValueError:
        try:
            clean = timestamp.replace("Z", "")
            return calendar.timegm(time.strptime(clean, "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            return None


def _transcript_is_recent(transcript_path: Path, *, cutoff_epoch: float | None) -> bool:
    """Mtime check — cheap pre-filter before the full JSONL scan."""
    if cutoff_epoch is None:
        return True
    try:
        return transcript_path.stat().st_mtime >= cutoff_epoch
    except OSError:
        return False


# ============================================================
# Aggregation
# ============================================================


def measure_project(project_dir: Path, *, cutoff_epoch: float | None = None) -> ProjectStats:
    """Walk every .jsonl in ``project_dir`` and tally tool usage."""
    stats = ProjectStats(project=project_dir.name)
    for transcript in project_dir.glob("*.jsonl"):
        if not _transcript_is_recent(transcript, cutoff_epoch=cutoff_epoch):
            continue
        stats.transcript_count += 1
        for tool_use in _iter_tool_uses(transcript):
            name = str(tool_use.get("name", ""))
            if not name:
                continue
            stats.tool_counts[name] += 1
    return stats


def measure_all_projects(root: Path, *, cutoff_epoch: float | None = None) -> list[ProjectStats]:
    """Sweep every project directory under ``root`` (e.g. ~/.claude/projects)."""
    if not root.exists():
        return []
    return [
        measure_project(p, cutoff_epoch=cutoff_epoch) for p in sorted(root.iterdir()) if p.is_dir()
    ]


def rollup(stats_list: list[ProjectStats]) -> ProjectStats:
    """Cross-project summary."""
    total = ProjectStats(project="__total__")
    for s in stats_list:
        total.transcript_count += s.transcript_count
        total.tool_counts.update(s.tool_counts)
    return total


# ============================================================
# CLI
# ============================================================


def render_human(stats_list: list[ProjectStats], total: ProjectStats) -> str:
    lines = ["# Agent tool-usage discipline report", ""]
    for s in stats_list:
        if s.transcript_count == 0:
            continue
        lines.append(f"## {s.project}  ({s.transcript_count} transcripts, verdict: {s.verdict()})")
        lines.append(
            f"  graph={s.graph_total}  read_grep={s.read_grep_total}  "
            f"edit={s.edit_total}  graph_share={s.graph_share:.2%}"
        )
        top = s.tool_counts.most_common(5)
        if top:
            lines.append("  top: " + ", ".join(f"{name}={n}" for name, n in top))
        lines.append("")
    lines.append(f"## TOTAL  ({total.transcript_count} transcripts, verdict: {total.verdict()})")
    lines.append(
        f"  graph={total.graph_total}  read_grep={total.read_grep_total}  "
        f"edit={total.edit_total}  graph_share={total.graph_share:.2%}"
    )
    lines.append("  target: graph_share >= 0.30 (plan), >= 0.50 (strong discipline)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure agent tool-usage discipline from Claude Code transcripts."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_TRANSCRIPT_ROOT,
        help="Root directory of Claude Code project transcripts.",
    )
    parser.add_argument(
        "--project",
        help="Limit to one project (directory name under --root).",
    )
    parser.add_argument(
        "--transcript",
        type=Path,
        help="Measure ONE specific transcript (.jsonl) instead of sweeping.",
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=0,
        help="Only count transcripts modified in the last N days (0 = all).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown.")
    args = parser.parse_args(argv)

    cutoff_epoch: float | None = None
    if args.since_days > 0:
        cutoff_epoch = time.time() - args.since_days * 86400

    if args.transcript:
        if not args.transcript.exists():
            sys.stderr.write(f"transcript not found: {args.transcript}\n")
            return 2
        stats = ProjectStats(project=args.transcript.stem)
        stats.transcript_count = 1
        for tool_use in _iter_tool_uses(args.transcript):
            name = str(tool_use.get("name", ""))
            if name:
                stats.tool_counts[name] += 1
        all_stats = [stats]
    elif args.project:
        project_dir = args.root / args.project
        if not project_dir.exists():
            sys.stderr.write(f"project dir not found: {project_dir}\n")
            return 2
        all_stats = [measure_project(project_dir, cutoff_epoch=cutoff_epoch)]
    else:
        all_stats = measure_all_projects(args.root, cutoff_epoch=cutoff_epoch)

    total = rollup(all_stats)

    if args.json:
        payload = {
            "projects": [s.to_dict() for s in all_stats if s.transcript_count > 0],
            "total": total.to_dict(),
        }
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(render_human(all_stats, total) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
