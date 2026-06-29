"""Batch D: CLI gate tests for scripts/golden_eval.py.

The gate must (a) pass against the committed baseline, (b) fail when EITHER
ranking metric (MRR@10 or NDCG@10) drops past the threshold -- including the
NDCG-only case that ``compare_metrics`` alone would miss, since it only flags
the primary metric -- and (c) tolerate a drop within the threshold.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts import golden_eval

from agent_memory_lite.evals.golden.runner import run_golden_eval


def _write_baseline(path: Path, **overrides: float) -> Path:
    """Build a baseline file from the live report with the given metric overrides."""
    report = dict(run_golden_eval())
    report.update(overrides)
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def test_json_run_returns_zero_and_emits_metrics(capsys: pytest.CaptureFixture[str]) -> None:
    assert golden_eval.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["queries_evaluated"] > 0
    for key in ("retrieval_mrr_at_10", "retrieval_ndcg_at_10", "retrieval_recall_at_10"):
        assert 0.0 <= payload[key] <= 1.0


def test_compare_against_committed_baseline_passes() -> None:
    # The default committed baseline equals the committed fixture -> no regression.
    assert golden_eval.main(["--compare-baseline"]) == 0


def test_save_baseline_roundtrips(tmp_path: Path) -> None:
    target = tmp_path / "baseline.json"
    assert golden_eval.main(["--save-baseline", str(target)]) == 0
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["retrieval_mrr_at_10"] >= 0.0
    # comparing against a just-saved baseline is always green.
    assert golden_eval.main(["--compare-baseline", str(target)]) == 0


def test_gate_fails_on_mrr_regression(tmp_path: Path) -> None:
    live = run_golden_eval()
    baseline = _write_baseline(
        tmp_path / "b.json", retrieval_mrr_at_10=live["retrieval_mrr_at_10"] + 0.2
    )
    assert golden_eval.main(["--compare-baseline", str(baseline)]) == 1


def test_gate_fails_on_ndcg_regression_when_mrr_unchanged(tmp_path: Path) -> None:
    """NDCG dropped past threshold while MRR is identical. compare_metrics only
    flags the PRIMARY (MRR), so this exercises the CLI's explicit NDCG check."""
    live = run_golden_eval()
    baseline = _write_baseline(
        tmp_path / "b.json", retrieval_ndcg_at_10=live["retrieval_ndcg_at_10"] + 0.2
    )
    assert golden_eval.main(["--compare-baseline", str(baseline)]) == 1


def test_gate_passes_when_drop_within_threshold(tmp_path: Path) -> None:
    live = run_golden_eval()
    # Baseline only 0.01 above live on both ranking metrics -> within the 0.05 gate.
    baseline = _write_baseline(
        tmp_path / "b.json",
        retrieval_mrr_at_10=live["retrieval_mrr_at_10"] + 0.01,
        retrieval_ndcg_at_10=live["retrieval_ndcg_at_10"] + 0.01,
    )
    assert golden_eval.main(["--compare-baseline", str(baseline)]) == 0
