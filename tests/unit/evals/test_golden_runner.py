"""Batch D: offline golden-set retrieval runner tests.

The golden runner seeds a FIXED corpus once and scores every labelled query's
recall/MRR/NDCG@10 over the shared pool. These tests pin the committed fixture
and the committed baseline together (they must not silently drift), and prove
the runner is deterministic and self-contained -- no HTTP service, no embedding
model, no ``mteb`` -- so it can gate CI.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.evals.golden.runner import (
    _CORPUS_KINDS,
    _WS,
    load_golden_set,
    run_golden_eval,
)
from agent_memory_lite.evals.metrics import recall_at_k
from agent_memory_lite.ingestion.canonical_writer import write_canonical
from agent_memory_lite.storage.reader import search

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BASELINE = _REPO_ROOT / "src" / "agent_memory_lite" / "evals" / "golden" / "baseline.json"
_METRIC_KEYS = (
    "retrieval_recall_at_10",
    "retrieval_mrr_at_10",
    "retrieval_ndcg_at_10",
)
_REQUIRED_PAYLOAD = {
    "decision": {"title", "decision_text"},
    "theory": {"title", "claim"},
    "concept": {"name", "definition"},
    "insight": {"summary", "insight_type", "status"},
}


# ============================================================
# fixture health
# ============================================================


def test_fixture_is_well_formed() -> None:
    """The committed fixture must have unique labels, resolvable relevances, and
    valid per-kind payloads -- a guard so a future edit cannot quietly break it."""
    data = load_golden_set()
    corpus = data["corpus"]
    queries = data["queries"]
    assert len(corpus) >= 40
    assert len(queries) >= 50  # the gate needs a broad query set to be meaningful

    labels = [row["label"] for row in corpus]
    assert len(labels) == len(set(labels)), "corpus labels must be globally unique"
    label_set = set(labels)

    for query in queries:
        assert query["relevant"], f"query {query['name']} has no relevant labels"
        for rel in query["relevant"]:
            assert rel in label_set, f"query {query['name']} references unknown label {rel}"

    for row in corpus:
        kind = row["kind"]
        assert kind in _REQUIRED_PAYLOAD, f"unknown kind {kind} in {row['label']}"
        missing = _REQUIRED_PAYLOAD[kind] - set(row["payload"])
        assert not missing, f"{row['label']} ({kind}) missing payload fields {missing}"


def test_every_query_is_retrievable() -> None:
    """No query may have recall@10 == 0: a fully-unreachable relevant row gives
    MRR == NDCG == 0 regardless of ranking, so it is pure noise, not signal, and
    must be pruned rather than committed."""
    data = load_golden_set()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    try:
        label_to_id: dict[str, str] = {}
        for row in data["corpus"]:
            out = write_canonical(
                conn, workspace_id=_WS, kind=str(row["kind"]), payload=dict(row["payload"])
            )
            assert out is not None
            label_to_id[str(row["label"])] = str(out["id"])
        for query in data["queries"]:
            expected = [label_to_id[label] for label in query["relevant"]]
            hits = search(
                conn,
                workspace_id=_WS,
                query=str(query["query"]),
                kinds=_CORPUS_KINDS,
                limit=10,
                log_coactivations=False,
            )
            retrieved = [str(h.projection["id"]) for h in hits]
            assert recall_at_k(retrieved, expected, k=10) > 0.0, (
                f"query {query['name']} retrieves none of its relevant rows -- prune it"
            )
    finally:
        conn.close()


# ============================================================
# runner behaviour
# ============================================================


def test_run_golden_eval_shape() -> None:
    data = load_golden_set()
    report = run_golden_eval()
    assert report["queries_evaluated"] == len(data["queries"])
    assert report["corpus_size"] == len(data["corpus"])
    for key in _METRIC_KEYS:
        assert 0.0 <= report[key] <= 1.0


def test_run_golden_eval_is_deterministic() -> None:
    assert run_golden_eval() == run_golden_eval()


def test_runner_uses_caller_connection_without_closing_it() -> None:
    """When handed a connection the runner seeds into it and leaves it open."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    try:
        report = run_golden_eval(conn)
        assert report["corpus_size"] >= 40
        # conn is still usable -> the runner did not close a caller-owned conn.
        seeded = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        assert seeded > 0
    finally:
        conn.close()


# ============================================================
# committed baseline must match the committed fixture
# ============================================================


# Ranking metrics are compared within this tolerance, not bit-exact: == on rounded
# FTS5/BM25 floats is needlessly brittle across SQLite/FTS5 builds (an exact-tie
# reorder can move a metric at the 4th decimal and redden CI with no real
# regression). The tolerance sits far below any genuine ranking regression (which
# moves the metrics by >>0.01 -- the CLI gate's own threshold is 0.05) yet absorbs
# platform tie-break noise. Structural drift (corpus_size / queries_evaluated) is
# still asserted EXACTLY below, so a fixture edit cannot silently desync the counts.
_BASELINE_METRIC_TOL = 0.01


def test_committed_baseline_matches_live_run() -> None:
    """The committed baseline.json must track a fresh run of the committed fixture:
    the corpus/query COUNTS exactly, and the ranking metrics within a small
    tolerance. If someone edits golden_set.yaml (adds/removes rows or queries, or
    shifts ranking) without re-saving the baseline, this fails -- keeping the CI
    gate's reference point honest while staying robust to cross-platform FTS5 ties."""
    baseline = json.loads(_BASELINE.read_text(encoding="utf-8"))
    report = run_golden_eval()
    # Structural counts must match exactly -- catches add/remove-row|query edits
    # that leave the rounded ranking metrics unmoved.
    for count_key in ("corpus_size", "queries_evaluated"):
        assert report[count_key] == baseline[count_key], (
            f"{count_key}: live {report[count_key]} != committed baseline "
            f"{baseline[count_key]} -- re-run scripts/golden_eval.py --save-baseline"
        )
    for key in _METRIC_KEYS:
        assert abs(report[key] - baseline[key]) <= _BASELINE_METRIC_TOL, (
            f"{key}: live {report[key]} vs committed baseline {baseline[key]} "
            f"(|delta| > {_BASELINE_METRIC_TOL}) "
            f"-- re-run scripts/golden_eval.py --save-baseline after editing the fixture"
        )


# ============================================================
# the gate must have teeth: a real ranking regression must trip it
# ============================================================


def test_disabling_fts_arm_trips_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The single most consequential durable-kind ranking regression is the
    FTS5/BM25 arm silently breaking so search() falls back to the LIKE
    token-overlap path. The HARD adversarial sets exist precisely so that drop
    moves MRR@10/NDCG@10 well past the gate threshold. This pins that sensitivity:
    if the corpus ever drifts back to a saturated (toothless) state where FTS and
    LIKE agree, this fails -- because then the CI gate could not catch the
    regression it exists to catch."""
    from agent_memory_lite.evals.retrieval_regression import (  # noqa: PLC0415
        DEFAULT_REGRESSION_THRESHOLD,
    )
    from agent_memory_lite.storage import reader as reader_mod  # noqa: PLC0415

    fts = run_golden_eval()
    # Force the LIKE fallback for the durable kinds (search() falls back when the
    # FTS arm returns nothing -- e.g. a writer that forgot to sync durable_fts).
    monkeypatch.setattr(reader_mod, "search_kind_fts", lambda *a, **k: [])
    like = run_golden_eval()
    mrr_drop = fts["retrieval_mrr_at_10"] - like["retrieval_mrr_at_10"]
    ndcg_drop = fts["retrieval_ndcg_at_10"] - like["retrieval_ndcg_at_10"]
    assert mrr_drop > DEFAULT_REGRESSION_THRESHOLD or ndcg_drop > DEFAULT_REGRESSION_THRESHOLD, (
        f"FTS->LIKE must trip the gate but did not: mrr_drop={mrr_drop:.4f} "
        f"ndcg_drop={ndcg_drop:.4f} threshold={DEFAULT_REGRESSION_THRESHOLD} "
        f"-- the corpus has gone saturated; add HARD adversarial sets"
    )
