"""Adoption telemetry from audit_log — is the agent following the rules?

Phase 1.6 of v2.2 consolidation. Phase 1.3 (MCP-wrapped Read/Grep) was
architecturally infeasible: MCP cannot intercept Claude's built-in
filesystem tools. The remaining adoption gap is therefore a discipline
problem, not an infrastructure one — this script measures it.

Read-only, never mutates. ``audit_log`` captures **only mutations**, so
read-side adoption (memory_search, memory_file_digest, memory_get_context)
is invisible from this report. For read telemetry use
``/memory/ui/state`` recent-events tail. The ratios here are mutation-
side discipline signals that map 1:1 to seed-pinned behavior_instructions:

* **link_after_write** — ``link_capability`` calls per
  (write_decision + write_theory + add_theory_evidence + write_experiment).
  Maps to the ``Link capability after every decision and theory write``
  seed rule. Expected ≥ 1.0 (one link per write minimum).
* **decision_provenance** — fraction of ``write_decision`` rows where the
  resulting ``decisions.source_episode_id`` is non-null. Maps to "every
  decision should have provenance". Expected ≥ 0.5 ideally.
* **candidate_triage** — (``promote_candidate`` +
  ``reject_candidate`` + ``memory_candidate.promoted_to_behavior``) /
  ``write_memory_candidate``. Maps to "review extraction candidates
  promptly". Expected ≥ 0.5 — half the candidates should be triaged
  within the window.

Each ratio is binned red/amber/green:
  red    < 0.3
  amber  0.3..0.6
  green  >= 0.6

(``link_after_write`` uses the same bucket but interpreted as fraction;
the 0.6 threshold means 60% of writes have at least one link. The
seed rule states 100% but real-world full compliance is rare; 60%
reflects the post-1.2.5 calibration target.)

Per-agent breakdown via ``audit_log.agent_id`` (1.3.0+).

Usage::

    python scripts/memory_adoption_report.py --workspace agentLight --days 7
    python scripts/memory_adoption_report.py --all-workspaces --days 30 --json
    python scripts/memory_adoption_report.py --workspace agentLight --by-agent
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_memory_lite.config.workspace_registry import WorkspaceRegistry

_DEFAULT_REGISTRY = Path.home() / ".agent_memory" / "workspaces.json"

# Mutation actions used in ratio computation.
LINK_ACTIONS: tuple[str, ...] = ("link_capability",)
LINKABLE_WRITE_ACTIONS: tuple[str, ...] = (
    "write_decision",
    "write_theory",
    "add_theory_evidence",
    "write_experiment",
)
DECISION_ACTION = "write_decision"
CANDIDATE_WRITE_ACTION = "write_memory_candidate"
CANDIDATE_TRIAGE_ACTIONS: tuple[str, ...] = (
    "promote_candidate",
    "reject_candidate",
    "memory_candidate.promoted_to_behavior",
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _bucket(ratio: float | None) -> str:
    if ratio is None:
        return "n/a"
    if ratio < 0.3:
        return "red"
    if ratio < 0.6:
        return "amber"
    return "green"


def _registry_for(args: argparse.Namespace) -> WorkspaceRegistry:
    return WorkspaceRegistry(Path(args.registry) if args.registry else _DEFAULT_REGISTRY)


def _resolve_targets(args: argparse.Namespace) -> list[tuple[str, Path]]:
    if args.all_workspaces:
        return [(e.id, Path(e.db_path)) for e in _registry_for(args).list() if e.db_path]
    if args.db_path:
        return [(args.workspace, args.db_path)]
    entry = _registry_for(args).get(args.workspace)
    if entry is None:
        print(f"workspace {args.workspace!r} not found in registry", file=sys.stderr)
        sys.exit(2)
    return [(args.workspace, Path(entry.db_path))]


def _action_counts_by_agent(
    conn: sqlite3.Connection, workspace_id: str, since_iso: str
) -> dict[str | None, dict[str, int]]:
    rows = conn.execute(
        "SELECT agent_id, action, COUNT(*) AS n "
        "FROM audit_log WHERE workspace_id = ? AND created_at >= ? "
        "GROUP BY agent_id, action",
        (workspace_id, since_iso),
    ).fetchall()
    out: dict[str | None, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        out[r["agent_id"]][r["action"]] = int(r["n"])
    return {k: dict(v) for k, v in out.items()}


def _decision_provenance_ratio(
    conn: sqlite3.Connection, workspace_id: str, since_iso: str
) -> tuple[int, int]:
    """Return (with_source_episode_id, total) over decisions written in window."""
    row = conn.execute(
        "SELECT SUM(CASE WHEN source_episode_id IS NOT NULL THEN 1 ELSE 0 END) AS with_src, "
        "       COUNT(*) AS total "
        "FROM decisions WHERE workspace_id = ? AND created_at >= ?",
        (workspace_id, since_iso),
    ).fetchone()
    return int(row["with_src"] or 0), int(row["total"] or 0)


def _ratios(counts: dict[str, int]) -> dict[str, dict[str, object]]:
    """Compute the mutation-side discipline ratios from a per-action map."""
    links = sum(counts.get(a, 0) for a in LINK_ACTIONS)
    linkable_writes = sum(counts.get(a, 0) for a in LINKABLE_WRITE_ACTIONS)
    candidate_writes = counts.get(CANDIDATE_WRITE_ACTION, 0)
    triages = sum(counts.get(a, 0) for a in CANDIDATE_TRIAGE_ACTIONS)
    link_ratio = links / linkable_writes if linkable_writes else None
    triage_ratio = triages / candidate_writes if candidate_writes else None
    return {
        "link_after_write": {
            "ratio": link_ratio,
            "bucket": _bucket(link_ratio),
            "links": links,
            "writes": linkable_writes,
        },
        "candidate_triage": {
            "ratio": triage_ratio,
            "bucket": _bucket(triage_ratio),
            "triages": triages,
            "candidates": candidate_writes,
        },
    }


def _format_one(label: str, ratios: dict[str, dict[str, object]]) -> str:
    """Pretty-print a per-metric ratio dict.

    Each metric stores its numerator and denominator under different keys
    (links/writes, triages/candidates, with_source/total_decisions). Pull
    them by metric name so adding a fourth metric doesn't require editing
    the formatter.
    """
    counts: dict[str, tuple[str, str]] = {
        "link_after_write": ("links", "writes"),
        "candidate_triage": ("triages", "candidates"),
        "decision_provenance": ("with_source", "total_decisions"),
    }
    lines = [f"{label}:"]
    for name, info in ratios.items():
        if info["ratio"] is None:
            lines.append(f"  {name:<28}  n/a (no eligible writes in window)")
            continue
        n_key, d_key = counts.get(name, ("n", "total"))
        n = info.get(n_key, 0)
        d = info.get(d_key, 0)
        lines.append(f"  {name:<28}  {info['ratio']:>5.2f}  [{info['bucket']}]  ({n}/{d})")
    return "\n".join(lines)


def _scan(targets: list[tuple[str, Path]], since_iso: str, by_agent: bool) -> dict:
    payload: dict = {"workspaces": {}}
    for ws_id, db_path in targets:
        if not db_path.exists():
            print(f"skip: {ws_id} db missing at {db_path}", file=sys.stderr)
            continue
        conn = _connect(db_path)
        try:
            per_agent = _action_counts_by_agent(conn, ws_id, since_iso)
            with_src, total = _decision_provenance_ratio(conn, ws_id, since_iso)
        finally:
            conn.close()
        agg: dict[str, int] = defaultdict(int)
        for ag_counts in per_agent.values():
            for action, n in ag_counts.items():
                agg[action] += n
        agg_ratios = _ratios(dict(agg))
        prov = with_src / total if total else None
        agg_ratios["decision_provenance"] = {
            "ratio": prov,
            "bucket": _bucket(prov),
            "with_source": with_src,
            "total_decisions": total,
        }
        ws_payload: dict = {"aggregate": {"counts": dict(agg), "ratios": agg_ratios}}
        if by_agent:
            ws_payload["by_agent"] = {
                (agent or "(unset)"): {"counts": counts, "ratios": _ratios(counts)}
                for agent, counts in per_agent.items()
            }
        payload["workspaces"][ws_id] = ws_payload
    return payload


def _emit_table(payload: dict, since_iso: str, days: int, by_agent: bool) -> None:
    print(f"window: last {days} days (since {since_iso})\n")
    for ws_id, ws_data in payload["workspaces"].items():
        print(f"=== workspace {ws_id!r} ===")
        print(_format_one("aggregate (all agents)", ws_data["aggregate"]["ratios"]))
        if by_agent and "by_agent" in ws_data:
            for agent, agent_data in ws_data["by_agent"].items():
                print()
                print(_format_one(f"agent {agent!r}", agent_data["ratios"]))
        print()
    print("legend:  green >= 0.60   amber 0.30..0.59   red < 0.30")
    print(
        "note: read-side adoption (memory_search, memory_file_digest, "
        "memory_get_context) is NOT in audit_log — use /memory/ui/state "
        "for read telemetry."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workspace", help="single workspace_id")
    parser.add_argument("--db-path", type=Path, help="explicit memory.db path")
    parser.add_argument(
        "--all-workspaces",
        action="store_true",
        help="report across every registered workspace",
    )
    parser.add_argument("--registry", help="override path to workspaces.json")
    parser.add_argument("--days", type=int, default=7, help="window in days (default 7)")
    parser.add_argument(
        "--by-agent",
        action="store_true",
        help="break down per agent_id from audit_log column",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    if not args.workspace and not args.all_workspaces:
        parser.error("--workspace or --all-workspaces is required")
    if args.workspace and args.all_workspaces:
        parser.error("--workspace and --all-workspaces are mutually exclusive")

    since_iso = (datetime.now(UTC) - timedelta(days=args.days)).isoformat()
    targets = _resolve_targets(args)
    if not targets:
        return 2
    payload = _scan(targets, since_iso, args.by_agent)
    payload["since"] = since_iso
    payload["days"] = args.days

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    _emit_table(payload, since_iso, args.days, args.by_agent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
