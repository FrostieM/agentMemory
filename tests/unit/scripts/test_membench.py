"""scripts/membench.py tests.

Locks the MVP contract:

* Empty workspace returns ``queries_evaluated=0``, no crash.
* When the search backend returns ground truth at rank 1 for every
  query, MRR = 1.0 and recall@5 = 1.0.
* Misses are surfaced in the ``misses`` field.
* NDCG@k math is correct for rank=1 / mid-list / off-list.
* ``_compare_baseline`` exits 1 when MRR drops more than 5% and
  exits 0 otherwise.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

# Import scripts/membench.py directly — it's not on sys.path as a package.
_SPEC = importlib.util.spec_from_file_location(
    "membench_under_test",
    Path(__file__).resolve().parents[3] / "scripts" / "membench.py",
)
assert _SPEC is not None
assert _SPEC.loader is not None
_mb = importlib.util.module_from_spec(_SPEC)
sys.modules["membench_under_test"] = _mb
_SPEC.loader.exec_module(_mb)


@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    """Schema-only DB with no decisions."""
    p = tmp_path / "empty.db"
    conn = sqlite3.connect(p)
    conn.executescript(
        """
        CREATE TABLE decisions (
            id TEXT PRIMARY KEY, workspace_id TEXT, title TEXT,
            status TEXT, updated_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    return p


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    """DB with three active decisions plus one archived (should be filtered)."""
    p = tmp_path / "seeded.db"
    conn = sqlite3.connect(p)
    conn.executescript(
        """
        CREATE TABLE decisions (
            id TEXT PRIMARY KEY, workspace_id TEXT, title TEXT,
            status TEXT, updated_at TEXT
        );
        INSERT INTO decisions VALUES
            ('dec_a', 'ws', 'alpha decision title', 'active',  '2026-05-20'),
            ('dec_b', 'ws', 'beta decision title',  'active',  '2026-05-20'),
            ('dec_c', 'ws', 'gamma decision title', 'active',  '2026-05-20'),
            ('dec_x', 'ws', 'archived title',       'archived','2026-05-20'),
            ('dec_n', 'ws', 'no',                   'active',  '2026-05-20');  -- < 4 chars
        """
    )
    conn.commit()
    conn.close()
    return p


def test_empty_workspace_returns_zero_metrics(empty_db: Path) -> None:
    """No active decisions → BenchmarkReport(0, 0.0, 0.0, 0.0)."""
    report = _mb.run_benchmark(db_path=empty_db, workspace_id="ws")
    assert report.queries_evaluated == 0
    assert report.mrr == 0.0
    assert report.recall_at_5 == 0.0


def test_perfect_search_yields_mrr_one(seeded_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the search backend always returns the expected id at rank 1,
    MRR = 1.0 and both recall@K are 1.0."""

    def fake_search(base_url, workspace_id, query, *, k, timeout):
        # Ground-truth oracle: the title carries 'alpha' → return dec_a, etc.
        if "alpha" in query:
            return ["dec_a", "other_id"]
        if "beta" in query:
            return ["dec_b", "other_id"]
        if "gamma" in query:
            return ["dec_c", "other_id"]
        return []

    monkeypatch.setattr(_mb, "_search", fake_search)
    report = _mb.run_benchmark(db_path=seeded_db, workspace_id="ws")
    assert report.queries_evaluated == 3  # the 4-char-min filter drops 'no'
    assert report.mrr == pytest.approx(1.0)
    assert report.recall_at_5 == pytest.approx(1.0)
    assert report.recall_at_10 == pytest.approx(1.0)
    assert report.misses == []


def test_partial_hit_reports_correct_mrr_and_misses(
    seeded_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One hit at rank 1, one at rank 3, one miss → MRR averages and
    misses lists the unfound id."""

    def fake_search(base_url, workspace_id, query, *, k, timeout):
        if "alpha" in query:
            return ["dec_a"]  # rank 1
        if "beta" in query:
            return ["x", "y", "dec_b"]  # rank 3
        return ["completely_other"]  # gamma misses

    monkeypatch.setattr(_mb, "_search", fake_search)
    report = _mb.run_benchmark(db_path=seeded_db, workspace_id="ws")
    # MRR = (1 + 1/3 + 0) / 3 = 0.4444
    assert report.mrr == pytest.approx((1 + 1 / 3 + 0) / 3, abs=0.0001)
    assert report.recall_at_5 == pytest.approx(2 / 3)
    assert "dec_c" in report.misses


def test_search_backend_failure_scores_as_miss(
    seeded_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the HTTP request errors out, _search returns [] — every
    query scores as a miss instead of crashing the run."""

    def broken_search(*args, **kwargs):
        return []

    monkeypatch.setattr(_mb, "_search", broken_search)
    report = _mb.run_benchmark(db_path=seeded_db, workspace_id="ws")
    assert report.mrr == 0.0
    assert len(report.misses) == 3  # all three active decisions miss


def test_ndcg_at_k_math() -> None:
    """rank=1 → NDCG=1.0; rank=2 → 1/log2(3); rank>k → 0."""
    import math  # noqa: PLC0415

    assert _mb._ndcg_at_k(1, k=10) == pytest.approx(1.0)
    assert _mb._ndcg_at_k(2, k=10) == pytest.approx(1.0 / math.log2(3))
    assert _mb._ndcg_at_k(11, k=10) == 0.0  # out of window
    assert _mb._ndcg_at_k(None, k=10) == 0.0  # miss


def test_compare_baseline_regression_exits_one(tmp_path: Path) -> None:
    """MRR drop > 5% → exit code 1."""
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"mrr": 0.90, "recall_at_5": 0.95, "recall_at_10": 0.95}))
    report = _mb.BenchmarkReport(
        workspace_id="ws",
        queries_evaluated=10,
        mrr=0.80,  # dropped 0.10 — well above 5% threshold
        recall_at_5=0.85,
        recall_at_10=0.90,
    )
    deltas, code = _mb._compare_baseline(report, baseline)
    assert code == 1
    assert deltas["mrr"] < -0.05


def test_compare_baseline_improvement_exits_zero(tmp_path: Path) -> None:
    """MRR improvement → exit 0."""
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"mrr": 0.80, "recall_at_5": 0.85, "recall_at_10": 0.90}))
    report = _mb.BenchmarkReport(
        workspace_id="ws",
        queries_evaluated=10,
        mrr=0.90,
        recall_at_5=0.95,
        recall_at_10=0.95,
    )
    deltas, code = _mb._compare_baseline(report, baseline)
    assert code == 0
    assert deltas["mrr"] > 0


def test_compare_baseline_missing_file_returns_empty(tmp_path: Path) -> None:
    """No baseline yet (first run) → empty deltas, exit 0."""
    report = _mb.BenchmarkReport("ws", 0, 0.0, 0.0, 0.0)
    deltas, code = _mb._compare_baseline(report, tmp_path / "no.json")
    assert deltas == {}
    assert code == 0


def test_format_text_includes_metrics(seeded_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Human output carries MRR + both recall numbers + workspace id."""
    monkeypatch.setattr(_mb, "_search", lambda *a, **k: ["dec_a", "dec_b", "dec_c"])
    report = _mb.run_benchmark(db_path=seeded_db, workspace_id="ws")
    text = _mb._format_text(report)
    assert "membench" in text
    assert "MRR" in text
    assert "recall@5" in text
    assert "ws" in text
