"""scripts/membench_external.py contract tests.

The actual benchmark run takes 5-15 minutes (downloads BEIR dataset
+ encodes 5k documents) so it's NOT in the CI gate. These tests pin
the contract that DOES live in CI: argparse interface works, the
format helper renders the comparison table, the reference scores
table is populated, and the script declares its missing-dep handling.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "membench_external.py"
_SPEC = importlib.util.spec_from_file_location("membench_external_test", _SCRIPT)
assert _SPEC is not None
assert _SPEC.loader is not None
_mb = importlib.util.module_from_spec(_SPEC)
sys.modules["membench_external_test"] = _mb
_SPEC.loader.exec_module(_mb)


def test_reference_scores_populated() -> None:
    """The reference snapshot has SciFact and NFCorpus rows so the
    comparison table is meaningful for the two MVP tasks we ship."""
    assert "SciFact" in _mb._REFERENCE_NDCG10
    assert "NFCorpus" in _mb._REFERENCE_NDCG10
    sci = _mb._REFERENCE_NDCG10["SciFact"]
    # The four canonical comparison rows: BM25, our model, OpenAI, Cohere.
    assert "BM25 baseline" in sci
    assert any("e5-small" in k for k in sci)
    assert "OpenAI text-embedding-ada-002" in sci


def test_format_summary_renders_metrics_and_published_scores() -> None:
    """A real-looking score dict produces a table with our number,
    the published reference number, and the delta annotation."""
    text = _mb._format_summary(
        "SciFact", {"ndcg_at_10": 0.68, "recall_at_10": 0.80, "map_at_10": 0.65}
    )
    assert "BEIR SciFact" in text
    assert "NDCG@10" in text
    assert "Recall@10" in text
    # The 'delta from published' annotation must appear for our model row.
    assert "delta from published" in text
    # All four reference systems present in the output table.
    assert "BM25 baseline" in text
    assert "ada-002" in text
    assert "Cohere" in text


def test_format_summary_unknown_task_still_renders() -> None:
    """A task that's not in the reference table still renders our
    own numbers — just without the comparison rows."""
    text = _mb._format_summary("UnknownTask", {"ndcg_at_10": 0.5})
    assert "UnknownTask" in text
    assert "NDCG@10" in text


def test_script_help_works() -> None:
    """`python scripts/membench_external.py --help` exits 0 and shows
    the --task / --model / --json flags. Catches argparse regressions
    without touching the heavy benchmark runtime."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    assert "--task" in result.stdout
    assert "--model" in result.stdout
    assert "--json" in result.stdout
