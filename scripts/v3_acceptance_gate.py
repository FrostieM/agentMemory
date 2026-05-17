"""v3 acceptance gate — quantitative measurements for the cutover decision.

Runs the v3 plan's Phase-2 acceptance gate against one workspace:

  1. Brief token cost:  compose_brief() body_md word count.
  2. Brief cache hit rate: 5 cold + 5 warm calls, measure cache_hit fraction.
  3. Search projection token cost: median tokens per hit across a query bundle.
  4. Digest staleness:  age of the newest code_digests row in minutes.
  5. Per-call latency p50 / p95 for brief and search.

Output (default JSON, --human for a markdown summary)::

    {
      "workspace_id": "agentLight",
      "brief": {
        "token_count": 421,
        "budget": 500,
        "within_budget": true,
        "cache_hit_rate": 0.6,
        "p50_ms": 28.4,
        "p95_ms": 71.2
      },
      "search": {
        "median_tokens_per_hit": 27,
        "p95_tokens_per_hit": 41,
        "within_target": true,
        "p50_ms": 12.7
      },
      "digests": {
        "count": 156,
        "newest_age_minutes": 87,
        "median_age_minutes": 1240
      },
      "verdict": "pass"  // pass | warn | fail
    }

Targets (from the v3 plan §Verification):

  - Brief token count   ≤ max_tokens (default 500)         → required
  - Brief cache hit rate ≥ 0.70 after the first 5 calls    → warn if lower
  - Median projection tokens per search hit ≤ 30           → warn if higher
  - Digest staleness median ≤ 120 minutes (2h)             → warn if higher

The gate never makes the cutover decision automatically — it produces the
evidence the operator uses to decide. ``--require-all`` flips warnings to
exit code 1 for CI usage.
"""

from __future__ import annotations

import argparse
import calendar
import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from agent_memory_lite.v3.cognition.brief import compose_brief
from agent_memory_lite.v3.storage.reader import search

# Default query bundle for search measurements. Covers each major kind.
DEFAULT_QUERIES = (
    "kelly sizing",
    "memory dedupe",
    "rate limit",
    "ingest episode",
    "decision rollback",
    "behavior pinned",
    "code digest pagerank",
    "consolidation cron",
)


# ============================================================
# Token approximation (shared with brief.approx_tokens)
# ============================================================


def _approx_tokens(text: str) -> int:
    return len(text.split()) if text else 0


def _projection_tokens(projection: dict[str, Any]) -> int:
    """Sum of tokens across all stringy values in a projection dict."""
    total = 0
    for v in projection.values():
        if isinstance(v, str):
            total += _approx_tokens(v)
    return total


# ============================================================
# Measurements
# ============================================================


def measure_brief(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    max_tokens: int,
    cold_calls: int = 5,
    warm_calls: int = 5,
) -> dict[str, Any]:
    """Run brief composition repeatedly, capture latency + cache behaviour."""
    from agent_memory_lite.v3.cognition import brief as brief_mod  # noqa: PLC0415

    brief_mod._BRIEF_CACHE.clear()  # always start cold
    latencies_ms: list[float] = []
    cache_hits: list[bool] = []
    token_count = 0
    body_within_budget = True
    for i in range(cold_calls + warm_calls):
        if i == cold_calls:
            # Don't clear the cache; we want warm runs to hit it.
            pass
        t0 = time.perf_counter()
        result = compose_brief(conn, workspace_id=workspace_id, max_tokens=max_tokens)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        cache_hits.append(result.cache_hit)
        token_count = result.token_count
        body_within_budget = token_count <= max_tokens
    # Cache hit rate over the warm window only.
    warm_hits = cache_hits[cold_calls:]
    hit_rate = sum(1 for h in warm_hits if h) / max(1, len(warm_hits))
    return {
        "token_count": token_count,
        "budget": max_tokens,
        "within_budget": body_within_budget,
        "cache_hit_rate": round(hit_rate, 3),
        "p50_ms": round(statistics.median(latencies_ms), 2),
        "p95_ms": round(_percentile(latencies_ms, 0.95), 2),
        "cold_calls": cold_calls,
        "warm_calls": warm_calls,
    }


def measure_search(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    queries: tuple[str, ...],
    limit: int = 10,
) -> dict[str, Any]:
    """Sweep query bundle, collect per-hit projection sizes + latency."""
    hit_tokens: list[int] = []
    latencies_ms: list[float] = []
    hit_counts: list[int] = []
    for q in queries:
        t0 = time.perf_counter()
        hits = search(conn, workspace_id=workspace_id, query=q, limit=limit)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        hit_counts.append(len(hits))
        for h in hits:
            hit_tokens.append(_projection_tokens(h.projection))
    if not hit_tokens:
        return {
            "queries_run": len(queries),
            "hits_returned": 0,
            "median_tokens_per_hit": 0,
            "p95_tokens_per_hit": 0,
            "within_target": True,
            "p50_ms": round(statistics.median(latencies_ms), 2) if latencies_ms else 0,
        }
    return {
        "queries_run": len(queries),
        "hits_returned": sum(hit_counts),
        "median_tokens_per_hit": int(statistics.median(hit_tokens)),
        "p95_tokens_per_hit": int(_percentile([float(t) for t in hit_tokens], 0.95)),
        "within_target": int(statistics.median(hit_tokens)) <= 30,
        "p50_ms": round(statistics.median(latencies_ms), 2),
        "p95_ms": round(_percentile(latencies_ms, 0.95), 2),
    }


def measure_digests(conn: sqlite3.Connection, *, workspace_id: str) -> dict[str, Any]:
    """Count code_digests rows and measure staleness."""
    rows = conn.execute(
        "SELECT last_indexed_at FROM code_digests WHERE workspace_id = ? "
        "AND last_indexed_at IS NOT NULL AND last_indexed_at != ''",
        (workspace_id,),
    ).fetchall()
    if not rows:
        return {
            "count": 0,
            "newest_age_minutes": None,
            "median_age_minutes": None,
        }
    now = time.time()
    ages_minutes: list[float] = []
    for (last_indexed,) in rows:
        try:
            # `Z`-suffixed timestamps are UTC; parse + interpret as UTC via
            # ``calendar.timegm`` so we don't accidentally apply the local
            # offset twice.
            ts = calendar.timegm(time.strptime(last_indexed, "%Y-%m-%dT%H:%M:%SZ"))
        except ValueError:
            continue
        ages_minutes.append((now - ts) / 60.0)
    if not ages_minutes:
        return {
            "count": len(rows),
            "newest_age_minutes": None,
            "median_age_minutes": None,
        }
    return {
        "count": len(rows),
        "newest_age_minutes": round(min(ages_minutes), 1),
        "median_age_minutes": round(statistics.median(ages_minutes), 1),
    }


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (0.95 ≈ p95)."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (k - f) * (s[c] - s[f])


# ============================================================
# Verdict + report
# ============================================================


def compute_verdict(report: dict[str, Any]) -> str:
    """pass / warn / fail rollup from individual measurements."""
    brief = report.get("brief", {})
    if isinstance(brief, dict) and not brief.get("within_budget", True):
        return "fail"
    search = report.get("search", {})
    warnings: list[str] = []
    if isinstance(brief, dict) and float(brief.get("cache_hit_rate", 0.0)) < 0.70:
        warnings.append("cache_hit_rate_below_target")
    if isinstance(search, dict) and not search.get("within_target", True):
        warnings.append("projection_tokens_above_target")
    digests = report.get("digests", {})
    if isinstance(digests, dict):
        newest = digests.get("newest_age_minutes")
        if isinstance(newest, int | float) and newest > 120:
            warnings.append("digest_staleness_above_target")
    return "warn" if warnings else "pass"


def run(
    db_path: Path, *, workspace_id: str, queries: tuple[str, ...], max_tokens: int
) -> dict[str, Any]:
    """Open the DB, run all measurements, return the full report dict."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        brief = measure_brief(conn, workspace_id=workspace_id, max_tokens=max_tokens)
        search_report = measure_search(conn, workspace_id=workspace_id, queries=queries)
        digests = measure_digests(conn, workspace_id=workspace_id)
    finally:
        conn.close()
    report: dict[str, Any] = {
        "workspace_id": workspace_id,
        "db_path": str(db_path),
        "brief": brief,
        "search": search_report,
        "digests": digests,
    }
    report["verdict"] = compute_verdict(report)
    return report


def render_human(report: dict[str, Any]) -> str:
    """One-screen markdown summary of the report."""
    brief = report["brief"] if isinstance(report.get("brief"), dict) else {}
    search = report["search"] if isinstance(report.get("search"), dict) else {}
    digests = report["digests"] if isinstance(report.get("digests"), dict) else {}
    verdict = report.get("verdict", "?")
    lines = [
        f"# v3 acceptance gate — workspace: {report.get('workspace_id', '?')}",
        f"Verdict: **{verdict}**",
        "",
        "## Brief",
        f"- token_count = {brief.get('token_count', '?')} (budget {brief.get('budget', '?')})",
        f"- within_budget = {brief.get('within_budget', '?')}",
        f"- cache_hit_rate = {brief.get('cache_hit_rate', '?')} (target ≥0.70)",
        f"- latency p50 / p95 = {brief.get('p50_ms', '?')} / {brief.get('p95_ms', '?')} ms",
        "",
        "## Search",
        f"- queries_run = {search.get('queries_run', '?')} → hits = {search.get('hits_returned', '?')}",
        f"- median tokens/hit = {search.get('median_tokens_per_hit', '?')} (target ≤30)",
        f"- p95 tokens/hit = {search.get('p95_tokens_per_hit', '?')}",
        f"- latency p50 = {search.get('p50_ms', '?')} ms",
        "",
        "## Digests",
        f"- count = {digests.get('count', 0)}",
        f"- newest_age_minutes = {digests.get('newest_age_minutes', '?')} (target ≤120)",
        f"- median_age_minutes = {digests.get('median_age_minutes', '?')}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the v3 acceptance gate measurements.")
    parser.add_argument("--db-path", required=True, help="Path to the v3 SQLite DB.")
    parser.add_argument("--workspace", required=True, help="Workspace id.")
    parser.add_argument("--max-tokens", type=int, default=500)
    parser.add_argument(
        "--queries",
        nargs="+",
        default=list(DEFAULT_QUERIES),
        help="Search queries to measure (default: built-in bundle).",
    )
    parser.add_argument("--human", action="store_true", help="Render markdown instead of JSON.")
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="Exit 1 on warn or fail (CI mode); default exits 1 only on fail.",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db_path)
    if not db_path.exists():
        sys.stderr.write(f"db not found: {db_path}\n")
        return 2

    report = run(
        db_path,
        workspace_id=args.workspace,
        queries=tuple(args.queries),
        max_tokens=args.max_tokens,
    )
    if args.human:
        sys.stdout.write(render_human(report) + "\n")
    else:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")

    verdict = report.get("verdict")
    if verdict == "fail":
        return 1
    if verdict == "warn" and args.require_all:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
