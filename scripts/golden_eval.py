"""CLI gate for the offline golden-set retrieval bench (Batch D).

Runs the deterministic, in-process golden set and either SAVES a baseline or
COMPARES against the committed one, FAILING (exit 1) when MRR@10 OR NDCG@10 drops
by more than the threshold (0.05) -- the committed-baseline retrieval regression
gate that turns a degraded-ranking PR red in CI.

    python scripts/golden_eval.py --json
    python scripts/golden_eval.py --save-baseline src/agent_memory_lite/evals/golden/baseline.json
    python scripts/golden_eval.py --compare-baseline src/agent_memory_lite/evals/golden/baseline.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_memory_lite.evals.golden.runner import run_golden_eval
from agent_memory_lite.evals.retrieval_regression import (
    DEFAULT_REGRESSION_THRESHOLD,
    compare_metrics,
)

# Gate on BOTH ranking metrics: compare_metrics only flags the PRIMARY, so we also
# inspect the NDCG delta. recall is reported for context (it is ~1.0 by design --
# the golden set is built so relevant rows are retrievable; ranking is the signal).
_GATE_METRICS = ["retrieval_mrr_at_10", "retrieval_ndcg_at_10", "retrieval_recall_at_10"]
_DEFAULT_BASELINE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "agent_memory_lite"
    / "evals"
    / "golden"
    / "baseline.json"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Golden-set retrieval MRR/NDCG@10 gate.")
    parser.add_argument("--save-baseline", type=Path, default=None)
    parser.add_argument(
        "--compare-baseline",
        nargs="?",
        type=Path,
        const=_DEFAULT_BASELINE,
        default=None,
        help="Compare against a baseline JSON (defaults to the committed baseline).",
    )
    parser.add_argument("--threshold", type=float, default=DEFAULT_REGRESSION_THRESHOLD)
    parser.add_argument(
        "--allow-regression",
        action="store_true",
        help="Permit --save-baseline to LOWER a committed metric (override the laundering guard).",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = run_golden_eval()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"golden retrieval: queries={report['queries_evaluated']} "
            f"corpus={report['corpus_size']} recall@10={report['retrieval_recall_at_10']} "
            f"mrr@10={report['retrieval_mrr_at_10']} ndcg@10={report['retrieval_ndcg_at_10']}"
        )

    if args.save_baseline is not None:
        # Anti-laundering (global-audit 2026-06-30): refuse to ratchet the baseline
        # DOWN -- otherwise a ranking regression could be hidden by simply re-saving
        # a lower baseline, defeating the compare gate. Mirrors the SLOC ratchet's
        # refuse-to-raise-a-cap. An intentional, justified lowering uses
        # --allow-regression (and should be explained in the commit).
        if args.save_baseline.exists() and not args.allow_regression:
            old = json.loads(args.save_baseline.read_text(encoding="utf-8"))
            lowered = [
                m
                for m in _GATE_METRICS
                if float(report.get(m, 0.0)) < float(old.get(m, 0.0)) - args.threshold
            ]
            if lowered:
                print(
                    f"REFUSED: saving this baseline would LOWER {lowered} by more than "
                    f"{args.threshold} vs the committed baseline -- this would launder a "
                    "ranking regression. Investigate the regression, or pass "
                    "--allow-regression with a justification in the commit."
                )
                return 1
        args.save_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.save_baseline.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"saved baseline -> {args.save_baseline}")
        return 0

    if args.compare_baseline is not None:
        baseline = json.loads(args.compare_baseline.read_text(encoding="utf-8"))
        result = compare_metrics(
            report,
            baseline,
            metrics=_GATE_METRICS,
            primary="retrieval_mrr_at_10",
            threshold=args.threshold,
        )
        mrr_d = result.deltas["retrieval_mrr_at_10"]
        ndcg_d = result.deltas["retrieval_ndcg_at_10"]
        recall_d = result.deltas["retrieval_recall_at_10"]
        print(f"deltas vs baseline: mrr={mrr_d:+.4f} ndcg={ndcg_d:+.4f} recall={recall_d:+.4f}")
        if mrr_d < -args.threshold or ndcg_d < -args.threshold:
            print(
                f"FAIL: retrieval regressed by more than {args.threshold} "
                f"(mrr {mrr_d:+.4f}, ndcg {ndcg_d:+.4f}) -- a ranking change degraded retrieval."
            )
            return 1
        print("OK: no retrieval regression beyond threshold.")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
