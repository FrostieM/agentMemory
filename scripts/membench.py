"""MVP retrieval-quality benchmark for the canonical v3 search surface."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_LIMIT = 10
DEFAULT_MAX_QUERIES = 100
_REGRESSION_THRESHOLD = 0.05


@dataclass
class BenchmarkReport:
    workspace_id: str
    queries_evaluated: int
    mrr: float
    recall_at_5: float
    recall_at_10: float
    misses: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["misses"] = self.misses[:5]
        return d


def _load_decisions(db_path: Path, workspace_id: str, *, limit: int) -> list[tuple[str, str]]:
    """Pull active decision ids and titles for literal-title recall checks."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, title FROM decisions "
            "WHERE workspace_id = ? AND status = 'active' AND title IS NOT NULL "
            "AND length(title) >= 4 "
            "ORDER BY updated_at DESC LIMIT ?",
            (workspace_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [(str(r[0]), str(r[1])) for r in rows if r[1]]


def _search(base_url: str, workspace_id: str, query: str, *, k: int, timeout: float) -> list[str]:
    """POST /memory/search and return ordered decision ids."""
    body = json.dumps(
        {"workspace_id": workspace_id, "kinds": ["decision"], "query": query, "limit": k}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/memory/search",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return []
    out: list[str] = []
    for hit in data.get("data") or []:
        if not isinstance(hit, dict):
            continue
        projection = hit.get("projection") if isinstance(hit.get("projection"), dict) else hit
        rid = projection.get("decision_id") or projection.get("id")
        if isinstance(rid, str):
            out.append(rid)
    return out


def _ndcg_at_k(rank: int | None, k: int) -> float:
    """Single-relevant-doc NDCG@K. rank is 1-indexed or None for miss."""
    if rank is None or rank > k:
        return 0.0
    import math  # noqa: PLC0415

    return 1.0 / math.log2(rank + 1)


def run_benchmark(
    *,
    db_path: Path,
    workspace_id: str,
    base_url: str = DEFAULT_BASE_URL,
    limit: int = DEFAULT_LIMIT,
    max_queries: int = DEFAULT_MAX_QUERIES,
    timeout: float = 10.0,
) -> BenchmarkReport:
    """Measure whether each active decision title retrieves its own row."""
    queries = _load_decisions(db_path, workspace_id, limit=max_queries)
    if not queries:
        return BenchmarkReport(workspace_id, 0, 0.0, 0.0, 0.0)
    rr_sum = 0.0
    hits_at_5 = 0
    hits_at_10 = 0
    misses: list[str] = []
    for did, title in queries:
        result_ids = _search(base_url, workspace_id, title, k=limit, timeout=timeout)
        rank: int | None = None
        for i, rid in enumerate(result_ids, start=1):
            if rid == did:
                rank = i
                break
        if rank is None:
            misses.append(did)
            continue
        rr_sum += 1.0 / rank
        if rank <= 5:
            hits_at_5 += 1
        if rank <= 10:
            hits_at_10 += 1
        _ndcg_at_k(rank, limit)
    n = len(queries)
    return BenchmarkReport(
        workspace_id=workspace_id,
        queries_evaluated=n,
        mrr=rr_sum / n,
        recall_at_5=hits_at_5 / n,
        recall_at_10=hits_at_10 / n,
        misses=misses,
    )


def _pct(v: float) -> str:
    return f"{v * 100:5.1f}%"


def _format_text(report: BenchmarkReport) -> str:
    lines = [
        "",
        f"membench - workspace={report.workspace_id!r}",
        "=" * 56,
        f"  queries evaluated: {report.queries_evaluated}",
        f"  MRR              : {report.mrr:.4f}",
        f"  recall@5         : {_pct(report.recall_at_5)}",
        f"  recall@10        : {_pct(report.recall_at_10)}",
    ]
    if report.misses:
        lines.append(f"  misses (top 5)   : {', '.join(report.misses[:5])}")
    return "\n".join(lines) + "\n"


def _compare_baseline(report: BenchmarkReport, baseline_path: Path) -> tuple[dict[str, float], int]:
    """Return (delta dict, exit_code). Exit 1 if MRR dropped by more than 5%."""
    try:
        prev = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, 0
    deltas = {
        "mrr": report.mrr - float(prev.get("mrr", 0.0)),
        "recall_at_5": report.recall_at_5 - float(prev.get("recall_at_5", 0.0)),
        "recall_at_10": report.recall_at_10 - float(prev.get("recall_at_10", 0.0)),
    }
    code = 1 if deltas["mrr"] < -_REGRESSION_THRESHOLD else 0
    return deltas, code


def main() -> int:
    parser = argparse.ArgumentParser(description="MemBench MVP retrieval quality baseline.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument(
        "--db-path",
        type=Path,
        help="Override DB path. Defaults to <project>/.agent_memory/memory.db.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text table.")
    parser.add_argument("--max-queries", type=int, default=DEFAULT_MAX_QUERIES)
    parser.add_argument(
        "--save-baseline",
        type=Path,
        help="After running, write the report JSON to this path for future comparison.",
    )
    parser.add_argument(
        "--compare-baseline",
        type=Path,
        help="Compare the run against a previously saved baseline; exit 1 if MRR drops >5%%.",
    )
    args = parser.parse_args()
    db_path = args.db_path or (Path(__file__).resolve().parents[1] / ".agent_memory" / "memory.db")
    if not db_path.exists():
        print(f"membench: DB not found: {db_path}", file=sys.stderr)
        return 2
    report = run_benchmark(
        db_path=db_path,
        workspace_id=args.workspace,
        base_url=args.base_url,
        max_queries=args.max_queries,
    )
    deltas: dict[str, float] = {}
    exit_code = 0
    if args.compare_baseline:
        deltas, exit_code = _compare_baseline(report, args.compare_baseline)
    if args.save_baseline:
        args.save_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.save_baseline.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    if args.json:
        payload = report.to_dict()
        if deltas:
            payload["baseline_diff"] = deltas
        print(json.dumps(payload, indent=2))
    else:
        print(_format_text(report))
        if deltas:
            print("  vs baseline:")
            for k, v in deltas.items():
                sign = "+" if v >= 0 else ""
                print(f"    {k:14s} {sign}{v:.4f}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
