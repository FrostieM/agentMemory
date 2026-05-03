"""Tuning sweep for MEMORY_FEEDBACK_HALFLIFE_DAYS.

Recomputes EWMA at multiple half-life values and runs the same A/B
ranking comparison the calibration report uses, so we can pick a
recommended default empirically rather than guessing.

Output: per-halflife summary of how many decisions changed top-K
position, average rank shift for the high-EWMA cohort, and the
biggest single rise/fall. The "best" halflife maximizes useful signal
without being so short that recent noise dominates.

Usage:
    python scripts/calibration/halflife_sweep.py \
        --db <path> --workspace <id> [--halflives 1,7,14,30,60]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from agent_memory_lite.retrieval.feedback_aggregator import (
    recompute_workspace_ewma,
)


def _recency(updated_at_iso: str | None) -> float:
    if not updated_at_iso:
        return 0.0
    try:
        ts = datetime.fromisoformat(updated_at_iso.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    age_days = max(0.0, (datetime.now(UTC) - ts).total_seconds() / 86400.0)
    return max(0.0, 1.0 - age_days / 90.0)


def run_one(conn: sqlite3.Connection, *, workspace: str, halflife: float) -> dict:
    """Recompute EWMA at the given half-life and rank decisions A/B."""
    # Reset EWMA columns first so the previous halflife doesn't leak.
    conn.execute(
        "UPDATE decisions SET feedback_ewma = 0.0 WHERE workspace_id = ?",
        (workspace,),
    )
    recompute_workspace_ewma(
        conn,
        workspace_id=workspace,
        source_type="decision",
        half_life_days=halflife,
        exclude_self_loop=True,
        max_per_day_per_source=10,
    )

    rows = conn.execute(
        "SELECT id, importance, confidence, feedback_ewma, updated_at "
        "FROM decisions WHERE workspace_id = ? AND status = 'active'",
        (workspace,),
    ).fetchall()

    items = []
    for r in rows:
        imp = float(r["importance"] or 0.0)
        conf = float(r["confidence"] or 0.0)
        ewma = float(r["feedback_ewma"] or 0.0)
        rec = _recency(r["updated_at"])
        score_off = 0.10 * imp + 0.10 * conf + 0.10 * rec
        score_on = score_off + 0.05 * ewma
        items.append({"id": r["id"], "ewma": ewma, "off": score_off, "on": score_on})

    by_off = sorted(items, key=lambda x: x["off"], reverse=True)
    by_on = sorted(items, key=lambda x: x["on"], reverse=True)
    rank_off = {x["id"]: i for i, x in enumerate(by_off)}
    rank_on = {x["id"]: i for i, x in enumerate(by_on)}

    moved = sum(1 for it in items if rank_off[it["id"]] != rank_on[it["id"]])
    deltas = [(rank_off[it["id"]] - rank_on[it["id"]]) for it in items]
    biggest_rise = max(deltas) if deltas else 0
    biggest_fall = min(deltas) if deltas else 0

    high = [x for x in items if x["ewma"] >= 0.5]
    low = [x for x in items if x["ewma"] < 0.5]
    avg_high_off = sum(rank_off[x["id"]] for x in high) / len(high) if high else 0.0
    avg_high_on = sum(rank_on[x["id"]] for x in high) / len(high) if high else 0.0
    avg_low_off = sum(rank_off[x["id"]] for x in low) / len(low) if low else 0.0
    avg_low_on = sum(rank_on[x["id"]] for x in low) / len(low) if low else 0.0

    ewma_present = sum(1 for x in items if x["ewma"] != 0.0)
    avg_abs_ewma = sum(abs(x["ewma"]) for x in items if x["ewma"] != 0.0) / max(1, ewma_present)

    return {
        "halflife": halflife,
        "n": len(items),
        "moved": moved,
        "ewma_present": ewma_present,
        "avg_abs_ewma": avg_abs_ewma,
        "biggest_rise": biggest_rise,
        "biggest_fall": biggest_fall,
        "high_delta": avg_high_off - avg_high_on,
        "low_delta": avg_low_off - avg_low_on,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument(
        "--halflives",
        default="1,7,14,30,60",
        help="comma-separated half-life-days values to sweep",
    )
    args = parser.parse_args()
    halflives = [float(x) for x in args.halflives.split(",")]

    conn = sqlite3.connect(Path(args.db), isolation_level=None)
    conn.row_factory = sqlite3.Row

    print(
        f"{'halflife':>8s} {'moved':>6s} {'ewma_n':>7s} {'avg|EWMA|':>10s} "
        f"{'big_rise':>9s} {'big_fall':>9s} {'high_d':>8s} {'low_d':>8s}"
    )
    print("-" * 75)
    results = []
    for hl in halflives:
        res = run_one(conn, workspace=args.workspace, halflife=hl)
        results.append(res)
        print(
            f"{res['halflife']:>8.1f} {res['moved']:>6d} {res['ewma_present']:>7d} "
            f"{res['avg_abs_ewma']:>10.3f} {res['biggest_rise']:>+9d} "
            f"{res['biggest_fall']:>+9d} {res['high_delta']:>+8.2f} "
            f"{res['low_delta']:>+8.2f}"
        )

    # Recommend: maximize biggest_rise * (-biggest_fall) — meaning we want
    # both directions to move strongly. Tiebreak on smallest avg|EWMA|
    # (means the EWMA is still moderate, not saturated).
    print()
    print("=== RECOMMENDATION ===")
    best = max(results, key=lambda r: r["biggest_rise"] * abs(r["biggest_fall"]))
    print(
        f"Suggested halflife: {best['halflife']:.0f} days "
        f"(biggest_rise={best['biggest_rise']:+d}, biggest_fall={best['biggest_fall']:+d})"
    )
    print()
    print("Interpretation: a high-EWMA / low-EWMA delta gap above ±20 positions")
    print("means the term carries real signal. avg|EWMA| near 1.0 means the")
    print("term is saturated (most rows hit the [-1, 1] bounds) — try shorter.")

    conn.close()


if __name__ == "__main__":
    main()
