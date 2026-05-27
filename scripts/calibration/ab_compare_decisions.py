"""A/B compare decision ranking: WITHOUT EWMA vs WITH EWMA.

Pure SQL + python — no HTTP, no embedding. Replicates the scoring formula
weights from retrieval/scoring.py so the comparison is exact.

Output: how many decisions changed top-K position, where high-EWMA items
moved, and an overall correlation summary.

Usage:
    python scripts/calibration/ab_compare_decisions.py \
        --db <path> --workspace <id>

Historical calibration evidence lives in git history.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def _recency(updated_at_iso: str | None) -> float:
    """Mimic the recency term: linear decay from 1.0 over 90 days."""
    if not updated_at_iso:
        return 0.0
    try:
        ts = datetime.fromisoformat(updated_at_iso.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    age_days = max(0.0, (datetime.now(UTC) - ts).total_seconds() / 86400.0)
    return max(0.0, 1.0 - age_days / 90.0)


def _score_items(rows: list) -> list[dict]:
    """Score each decision with and without the EWMA term."""
    items: list[dict] = []
    for r in rows:
        imp = float(r["importance"] or 0.0)
        conf = float(r["confidence"] or 0.0)
        ewma = float(r["feedback_ewma"] or 0.0)
        rec = _recency(r["updated_at"])
        score_off = 0.10 * imp + 0.10 * conf + 0.10 * rec
        items.append(
            {
                "id": r["id"],
                "title": (r["title"] or "")[:60],
                "imp": imp,
                "ewma": ewma,
                "score_off": score_off,
                "score_on": score_off + 0.05 * ewma,
            }
        )
    return items


def _rank_and_delta(items: list[dict]) -> tuple[dict, dict, int, int]:
    """Compute per-id rank deltas and aggregate up/down counts."""
    by_off = sorted(items, key=lambda x: x["score_off"], reverse=True)
    by_on = sorted(items, key=lambda x: x["score_on"], reverse=True)
    rank_off = {x["id"]: i for i, x in enumerate(by_off)}
    rank_on = {x["id"]: i for i, x in enumerate(by_on)}
    moved_up = moved_down = 0
    for it in items:
        delta = rank_off[it["id"]] - rank_on[it["id"]]
        it["delta"] = delta
        if delta > 0:
            moved_up += 1
        elif delta < 0:
            moved_down += 1
    return rank_off, rank_on, moved_up, moved_down


def _print_top10(items: list[dict], score_key: str, label: str) -> None:
    by_score = sorted(items, key=lambda x: x[score_key], reverse=True)
    print()
    print(f"=== TOP-10 {label} ===")
    for i, x in enumerate(by_score[:10]):
        print(
            f"  #{i + 1:2d} score={x[score_key]:.4f} ewma={x['ewma']:+.3f} "
            f"imp={x['imp']:.2f} {x['title']}"
        )


def _print_extremes(items: list[dict], rank_off: dict, rank_on: dict) -> None:
    print()
    print("=== BIGGEST RISERS (ewma boost helped most) ===")
    for x in sorted(items, key=lambda x: x["delta"], reverse=True)[:5]:
        if x["delta"] <= 0:
            break
        print(
            f"  +{x['delta']:2d} positions  ewma={x['ewma']:+.3f} "
            f"imp={x['imp']:.2f} #{rank_off[x['id']] + 1}->#{rank_on[x['id']] + 1}  "
            f"{x['title']}"
        )
    print()
    print("=== BIGGEST FALLERS ===")
    for x in sorted(items, key=lambda x: x["delta"])[:5]:
        if x["delta"] >= 0:
            break
        print(
            f"  {x['delta']:2d} positions  ewma={x['ewma']:+.3f} "
            f"imp={x['imp']:.2f} #{rank_off[x['id']] + 1}->#{rank_on[x['id']] + 1}  "
            f"{x['title']}"
        )


def _print_correlation(items: list[dict], rank_off: dict, rank_on: dict) -> None:
    high = [x for x in items if x["ewma"] >= 0.5]
    low = [x for x in items if x["ewma"] < 0.5]
    if not (high and low):
        return
    print()
    print("=== EWMA-VS-RANK CORRELATION ===")
    for label, subset in [("high-EWMA (>=0.5)", high), ("low-EWMA  (<0.5) ", low)]:
        avg_off = sum(rank_off[x["id"]] for x in subset) / len(subset)
        avg_on = sum(rank_on[x["id"]] for x in subset) / len(subset)
        print(
            f"  {label}  n={len(subset):3d}  "
            f"avg_rank_off={avg_off:.1f}  avg_rank_on={avg_on:.1f}  "
            f"delta={avg_off - avg_on:+.2f}"
        )
    print()
    print("  Interpretation: positive delta for high-EWMA group means the")
    print("  EWMA term moved operator-endorsed decisions toward the top.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="SQLite DB path")
    parser.add_argument("--workspace", required=True, help="workspace_id")
    args = parser.parse_args()

    conn = sqlite3.connect(Path(args.db), isolation_level=None)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, importance, confidence, feedback_ewma, status, updated_at, title "
        "FROM decisions WHERE workspace_id = ? AND status = 'active'",
        (args.workspace,),
    ).fetchall()
    print(f"active decisions: {len(rows)}")

    items = _score_items(rows)
    rank_off, rank_on, moved_up, moved_down = _rank_and_delta(items)
    print()
    print(
        f"positions changed: {moved_up + moved_down} / {len(items)} "
        f"(up={moved_up}, down={moved_down})"
    )
    _print_top10(items, "score_off", "OFF (current production formula)")
    _print_top10(items, "score_on", "ON  (with v1.4 EWMA term)")
    _print_extremes(items, rank_off, rank_on)
    _print_correlation(items, rank_off, rank_on)
    conn.close()


if __name__ == "__main__":
    main()
