"""Phase 0 (continuous): organ-era telemetry across all 7 v3.0.0-final phases.

Read-only health/effectiveness dashboard for the "memory as organ"
features. Pair-reads with ``scripts/memory_adoption_report.py`` (which
covers the v2/v3 discipline ratios) -- this one focuses on whether the
new organ-level loops are actually accruing signal:

* Phase 1: outcome_score distribution + percent of rows with non-zero score
* Phase 2: soft_edges count by edge_kind, retrieval_coactivation lag
* Phase 3: insights by status + behavior_reinforcement count
* Phase 4: reflex_rules active count + block_count / advisory_count ratio
* Phase 5: self_model presence + refreshed_via + coverage_score
* Phase 6: percent of rows with explicit valid_to (i.e. "facts that aged out")
* Phase 7: causal_links count by relation

Usage::

    python scripts/organ_metrics.py --workspace copyBot
    python scripts/organ_metrics.py --workspace copyBot --json
    python scripts/organ_metrics.py --all-workspaces --json

The script is local-only, never mutates, and uses the same workspace
registry as the rest of the toolchain.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# ============================================================
# Helpers
# ============================================================

_DEFAULT_REGISTRY = Path.home() / ".agent_memory" / "workspaces.json"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_query_one(
    conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()
) -> sqlite3.Row | None:
    try:
        return conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        return None


def _safe_query_all(
    conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()
) -> list[sqlite3.Row]:
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


# ============================================================
# Per-phase metric collectors
# ============================================================


def phase1_outcome_metrics(conn: sqlite3.Connection, workspace: str) -> dict[str, Any]:
    """Count rows by outcome_score band across the six outcome-bearing kinds."""
    out: dict[str, Any] = {}
    for table, label in [
        ("decisions", "decision"),
        ("theories", "theory"),
        ("behaviors", "behavior"),
        ("skills", "skill"),
        ("insights", "insight"),
        ("chunks", "chunk"),
    ]:
        row = _safe_query_one(
            conn,
            f"""SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN outcome_score > 0.0 THEN 1 ELSE 0 END) AS positive,
                SUM(CASE WHEN outcome_score < 0.0 THEN 1 ELSE 0 END) AS negative,
                SUM(CASE WHEN outcome_score = 0.0 THEN 1 ELSE 0 END) AS neutral,
                AVG(outcome_score) AS mean
              FROM {table} WHERE workspace_id = ?""",
            (workspace,),
        )
        if row is None:
            continue
        out[label] = {
            "total": int(row["total"] or 0),
            "positive": int(row["positive"] or 0),
            "negative": int(row["negative"] or 0),
            "neutral": int(row["neutral"] or 0),
            "mean_outcome": round(float(row["mean"] or 0.0), 3),
        }
    return out


def phase2_hebbian_metrics(conn: sqlite3.Connection, workspace: str) -> dict[str, Any]:
    """Soft-edge accumulation by kind + coactivation log size."""
    out: dict[str, Any] = {"soft_edges_by_kind": {}, "coactivation_rows": 0}
    edge_rows = _safe_query_all(
        conn,
        "SELECT edge_kind, COUNT(*) AS n, AVG(weight) AS w FROM soft_edges "
        "WHERE workspace_id = ? GROUP BY edge_kind",
        (workspace,),
    )
    for row in edge_rows:
        out["soft_edges_by_kind"][row["edge_kind"]] = {
            "count": int(row["n"]),
            "avg_weight": round(float(row["w"] or 0.0), 3),
        }
    coact = _safe_query_one(
        conn,
        "SELECT COUNT(*) AS n FROM retrieval_coactivation WHERE workspace_id = ?",
        (workspace,),
    )
    if coact is not None:
        out["coactivation_rows"] = int(coact["n"] or 0)
    return out


def phase3_consolidation_metrics(conn: sqlite3.Connection, workspace: str) -> dict[str, Any]:
    """Insights status + behavior_reinforcement breakdown."""
    by_status: dict[str, int] = {}
    for row in _safe_query_all(
        conn,
        "SELECT status, COUNT(*) AS n FROM insights WHERE workspace_id = ? GROUP BY status",
        (workspace,),
    ):
        by_status[row["status"]] = int(row["n"])
    by_type: dict[str, int] = {}
    for row in _safe_query_all(
        conn,
        "SELECT insight_type, COUNT(*) AS n FROM insights "
        "WHERE workspace_id = ? GROUP BY insight_type",
        (workspace,),
    ):
        by_type[row["insight_type"] or "unknown"] = int(row["n"])
    return {"insights_by_status": by_status, "insights_by_type": by_type}


def phase4_reflex_metrics(conn: sqlite3.Connection, workspace: str) -> dict[str, Any]:
    """Reflex rules + block/advisory fire counters."""
    out: dict[str, Any] = {
        "active_rules": 0,
        "by_enforcement": {},
        "block_fires": 0,
        "advisory_fires": 0,
    }
    rows = _safe_query_all(
        conn,
        "SELECT enforcement, COUNT(*) AS n, "
        "SUM(block_count) AS bf, SUM(advisory_count) AS af "
        "FROM reflex_rules WHERE workspace_id = ? AND active = 1 "
        "GROUP BY enforcement",
        (workspace,),
    )
    for row in rows:
        out["active_rules"] += int(row["n"])
        out["by_enforcement"][row["enforcement"]] = int(row["n"])
        out["block_fires"] += int(row["bf"] or 0)
        out["advisory_fires"] += int(row["af"] or 0)
    return out


def phase5_self_model_metrics(conn: sqlite3.Connection, workspace: str) -> dict[str, Any]:
    """Self-model presence + last refresh path + coverage."""
    row = _safe_query_one(
        conn,
        "SELECT identity_text, refreshed_via, coverage_score, updated_at "
        "FROM self_model WHERE workspace_id = ?",
        (workspace,),
    )
    if row is None:
        return {"present": False}
    return {
        "present": True,
        "refreshed_via": row["refreshed_via"],
        "coverage_score": round(float(row["coverage_score"] or 0.0), 3),
        "updated_at": row["updated_at"],
        "identity_word_count": len((row["identity_text"] or "").split()),
    }


def phase6_bi_temporal_metrics(conn: sqlite3.Connection, workspace: str) -> dict[str, Any]:
    """Rows with explicit valid_to (aged out) vs open-ended."""
    out: dict[str, Any] = {}
    for table, label in [
        ("decisions", "decision"),
        ("theories", "theory"),
        ("behaviors", "behavior"),
        ("concepts", "concept"),
        ("insights", "insight"),
    ]:
        row = _safe_query_one(
            conn,
            f"""SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN valid_to IS NOT NULL THEN 1 ELSE 0 END) AS aged_out
              FROM {table} WHERE workspace_id = ?""",
            (workspace,),
        )
        if row is None:
            continue
        total = int(row["total"] or 0)
        aged = int(row["aged_out"] or 0)
        out[label] = {
            "total": total,
            "aged_out": aged,
            "pct_aged": round(100.0 * aged / total, 1) if total else 0.0,
        }
    return out


def phase7_causal_metrics(conn: sqlite3.Connection, workspace: str) -> dict[str, Any]:
    """Causal-link count by relation."""
    by_relation: dict[str, int] = {}
    for row in _safe_query_all(
        conn,
        "SELECT relation, COUNT(*) AS n FROM causal_links WHERE workspace_id = ? GROUP BY relation",
        (workspace,),
    ):
        by_relation[row["relation"]] = int(row["n"])
    return {"causal_links_by_relation": by_relation}


# ============================================================
# Top-level report assembly
# ============================================================


def collect_workspace_report(db_path: Path, workspace: str) -> dict[str, Any]:
    """Run every phase metric collector for one workspace."""
    if not db_path.exists():
        return {"workspace_id": workspace, "error": "db_missing", "db_path": str(db_path)}
    conn = _connect(db_path)
    try:
        report: dict[str, Any] = {
            "workspace_id": workspace,
            "db_path": str(db_path),
            "phase_1_outcome": phase1_outcome_metrics(conn, workspace),
            "phase_2_hebbian": phase2_hebbian_metrics(conn, workspace),
            "phase_3_consolidation": phase3_consolidation_metrics(conn, workspace),
            "phase_4_reflex": phase4_reflex_metrics(conn, workspace),
            "phase_5_self_model": phase5_self_model_metrics(conn, workspace),
            "phase_6_bi_temporal": phase6_bi_temporal_metrics(conn, workspace),
            "phase_7_causal": phase7_causal_metrics(conn, workspace),
        }
    finally:
        conn.close()
    return report


def _iter_workspaces(registry_path: Path) -> Iterator[tuple[str, Path]]:
    if not registry_path.exists():
        return iter([])
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    seen: set[str] = set()
    out: list[tuple[str, Path]] = []
    for entry in payload.get("workspaces", []):
        ws = str(entry.get("id") or "")
        db = str(entry.get("db_path") or "")
        if not ws or not db or ws in seen:
            continue
        seen.add(ws)
        out.append((ws, Path(db)))
    return iter(out)


def _render_phase1(p1: dict[str, Any]) -> list[str]:
    if not p1:
        return []
    out = ["\n## Phase 1 -- outcome_score distribution"]
    for kind, stats in p1.items():
        if stats["total"] == 0:
            continue
        out.append(
            f"  {kind}: total={stats['total']}  "
            f"pos={stats['positive']}  neg={stats['negative']}  "
            f"neutral={stats['neutral']}  mean={stats['mean_outcome']:+.3f}"
        )
    return out


def _render_phase2(p2: dict[str, Any]) -> list[str]:
    edges = p2.get("soft_edges_by_kind", {})
    if not edges and not p2.get("coactivation_rows"):
        return []
    out = ["\n## Phase 2 -- Hebbian co-retrieval"]
    for kind, s in edges.items():
        out.append(f"  {kind}: {s['count']} edges  avg_weight={s['avg_weight']:.3f}")
    out.append(f"  retrieval_coactivation rows: {p2.get('coactivation_rows', 0)}")
    return out


def _render_phase3(p3: dict[str, Any]) -> list[str]:
    if not (p3.get("insights_by_status") or p3.get("insights_by_type")):
        return []
    out = ["\n## Phase 3 -- consolidation insights"]
    for status, n in p3.get("insights_by_status", {}).items():
        out.append(f"  status={status}: {n}")
    for typ, n in p3.get("insights_by_type", {}).items():
        out.append(f"  type={typ}: {n}")
    return out


def _render_phase4(p4: dict[str, Any]) -> list[str]:
    if not p4.get("active_rules"):
        return []
    out = ["\n## Phase 4 -- reflexes", f"  active rules: {p4['active_rules']}"]
    for enf, n in p4.get("by_enforcement", {}).items():
        out.append(f"  enforcement={enf}: {n}")
    out.append(f"  fires: block={p4.get('block_fires', 0)}  advisory={p4.get('advisory_fires', 0)}")
    return out


def _render_phase5(p5: dict[str, Any]) -> list[str]:
    if p5.get("present"):
        return [
            "\n## Phase 5 -- self-model",
            (
                f"  refreshed_via={p5['refreshed_via']}  "
                f"coverage={p5['coverage_score']:.3f}  "
                f"identity_words={p5['identity_word_count']}  "
                f"updated_at={p5['updated_at']}"
            ),
        ]
    if p5.get("present") is False:
        return ["\n## Phase 5 -- self-model: NOT YET REFRESHED"]
    return []


def _render_phase6(p6: dict[str, Any]) -> list[str]:
    if not p6:
        return []
    out = ["\n## Phase 6 -- bi-temporal aging"]
    any_row = False
    for kind, s in p6.items():
        if s["total"] == 0:
            continue
        any_row = True
        out.append(f"  {kind}: total={s['total']}  aged_out={s['aged_out']} ({s['pct_aged']:.1f}%)")
    return out if any_row else []


def _render_phase7(p7: dict[str, Any]) -> list[str]:
    relations = p7.get("causal_links_by_relation", {})
    if not relations:
        return []
    out = ["\n## Phase 7 -- causal graph"]
    for rel, n in relations.items():
        out.append(f"  {rel}: {n}")
    return out


def render_human(report: dict[str, Any]) -> str:
    """Compact text rendering. Hides empty phase blocks."""
    lines = [f"# Organ metrics: {report['workspace_id']}"]
    if "error" in report:
        lines.append(f"  ERROR: {report['error']} (db={report['db_path']})")
        return "\n".join(lines)
    lines.extend(_render_phase1(report.get("phase_1_outcome", {})))
    lines.extend(_render_phase2(report.get("phase_2_hebbian", {})))
    lines.extend(_render_phase3(report.get("phase_3_consolidation", {})))
    lines.extend(_render_phase4(report.get("phase_4_reflex", {})))
    lines.extend(_render_phase5(report.get("phase_5_self_model", {})))
    lines.extend(_render_phase6(report.get("phase_6_bi_temporal", {})))
    lines.extend(_render_phase7(report.get("phase_7_causal", {})))
    return "\n".join(lines)


# ============================================================
# Entrypoint
# ============================================================


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--workspace", help="Workspace id (omit with --all-workspaces).")
    parser.add_argument("--all-workspaces", action="store_true")
    parser.add_argument(
        "--registry",
        type=Path,
        default=_DEFAULT_REGISTRY,
        help=f"Workspace registry JSON (default: {_DEFAULT_REGISTRY}).",
    )
    parser.add_argument("--json", action="store_true", help="JSON output instead of text.")
    args = parser.parse_args()

    if not args.all_workspaces and not args.workspace:
        parser.error("either --workspace or --all-workspaces is required")

    reports: list[dict[str, Any]] = []
    if args.all_workspaces:
        for ws, db in _iter_workspaces(args.registry):
            reports.append(collect_workspace_report(db, ws))
    else:
        # Map workspace id -> db path via registry.
        db_for_ws: Path | None = None
        for ws, db in _iter_workspaces(args.registry):
            if ws == args.workspace:
                db_for_ws = db
                break
        if db_for_ws is None:
            print(
                f"workspace {args.workspace!r} not found in {args.registry}",
                file=sys.stderr,
            )
            return 1
        reports.append(collect_workspace_report(db_for_ws, args.workspace))

    if args.json:
        print(json.dumps({"reports": reports}, indent=2))
    else:
        for r in reports:
            print(render_human(r))
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
