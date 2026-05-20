"""scripts/membench_pipeline.py contract tests.

The actual pipeline benchmark needs a running HTTP service and BEIR
download (5k corpus docs, ~4h ingestion at full size). That's NOT
in the CI gate. This file pins the contract that IS:

- argparse interface compiles and rejects nothing required to live
- the smart-slice helper produces the right corpus+queries shape
- the relevance-grading helpers (NDCG / Recall / AP) match canonical
  values on hand-built rels arrays
- the BEIR-id extractor walks every metadata shape we accept
- ``--help`` exits 0 (catches argparse breakage)
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "membench_pipeline.py"
_SPEC = importlib.util.spec_from_file_location("membench_pipeline_test", _SCRIPT)
assert _SPEC is not None
assert _SPEC.loader is not None
_mb = importlib.util.module_from_spec(_SPEC)
sys.modules["membench_pipeline_test"] = _mb
_SPEC.loader.exec_module(_mb)


def test_script_help_works() -> None:
    """``python scripts/membench_pipeline.py --help`` exits 0 and lists
    the documented flags. Catches argparse regressions without touching
    the heavy benchmark runtime."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    assert "--task" in result.stdout
    assert "--queries" in result.stdout
    assert "--db-path" in result.stdout  # isolation flag must stay documented
    assert "--no-smart-slice" in result.stdout


def test_ndcg_at_k_handles_perfect_ordering() -> None:
    """All-relevant returns NDCG@k=1.0."""
    assert _mb._ndcg_at_k([1, 1, 1, 1, 1], 5) == 1.0


def test_ndcg_at_k_handles_empty() -> None:
    """No relevant doc anywhere → NDCG=0 (avoid divide-by-zero)."""
    assert _mb._ndcg_at_k([0, 0, 0], 5) == 0.0


def test_ndcg_at_k_position_penalty() -> None:
    """Relevant at rank 2 scores lower than at rank 1. Concrete
    canonical values for the single-relevant case so a regression
    in the log2 weighting is caught."""
    # 1 / log2(2) = 1.0 with idealised DCG also 1.0 → NDCG=1.0
    assert _mb._ndcg_at_k([1, 0], 2) == 1.0
    # Relevant at rank 2: DCG = 1/log2(3); ideal DCG = 1/log2(2) = 1.0
    import math  # noqa: PLC0415

    expected = (1.0 / math.log2(3)) / 1.0
    assert abs(_mb._ndcg_at_k([0, 1], 2) - expected) < 1e-9


def test_recall_at_k_basic() -> None:
    """Recall@5 with 2 relevant docs total, 1 of them in the top-5."""
    assert _mb._recall_at_k([1, 0, 0, 0, 0], total_relevant=2, k=5) == 0.5
    assert _mb._recall_at_k([], total_relevant=2, k=5) == 0.0
    # Zero relevant docs in the universe → recall undefined; return 0
    # rather than NaN so the average aggregator keeps moving.
    assert _mb._recall_at_k([1, 1, 1], total_relevant=0, k=10) == 0.0


def test_ap_handles_basic_ordering() -> None:
    """AP for [1, 0, 1, 0, 0] is (1/1 + 2/3) / 2 = 0.833..."""
    expected = (1.0 + 2.0 / 3.0) / 2.0
    assert abs(_mb._ap([1, 0, 1, 0, 0]) - expected) < 1e-9
    # All zeros → AP=0 (no hits, no division by zero).
    assert _mb._ap([0, 0, 0]) == 0.0


def test_extract_beir_id_walks_metadata_shapes() -> None:
    """The script accepts beir_doc_id under metadata / episode_metadata
    / meta keys to absorb the different hit shapes the search route
    emits across surfaces."""
    assert _mb._extract_beir_id({"metadata": {"beir_doc_id": "doc1"}}) == "doc1"
    assert _mb._extract_beir_id({"episode_metadata": {"beir_doc_id": "doc2"}}) == "doc2"
    assert _mb._extract_beir_id({"meta": {"beir_doc_id": "doc3"}}) == "doc3"
    # Int ids get stringified — BEIR ships ids as both str and int in the wild.
    assert _mb._extract_beir_id({"metadata": {"beir_doc_id": 42}}) == "42"
    # Missing metadata → None, NOT raise.
    assert _mb._extract_beir_id({"id": "no_meta_here"}) is None
    assert _mb._extract_beir_id({"metadata": "not_a_dict"}) is None


def test_smart_slice_picks_relevant_docs_plus_distractors() -> None:
    """Smart slice must include every relevant doc for the picked
    queries — that's the whole point of the optimisation (avoids the
    naïve corpus[:N] failure mode where qrels are sparse)."""
    # Fake mteb-shaped corpus + queries + qrels structure
    corpus = [
        {"id": "doc_rel_1", "title": "x", "text": "x"},
        {"id": "doc_rel_2", "title": "x", "text": "x"},
        {"id": "doc_distract_1", "title": "x", "text": "x"},
        {"id": "doc_distract_2", "title": "x", "text": "x"},
        {"id": "doc_distract_3", "title": "x", "text": "x"},
        {"id": "doc_distract_4", "title": "x", "text": "x"},
    ]
    queries = [
        {"id": "q1", "text": "first query"},
        {"id": "q2", "text": "second query"},
    ]
    qrels = {
        "q1": {"doc_rel_1": 1},
        "q2": {"doc_rel_2": 1},
    }
    slice_corpus, slice_queries = _mb._pick_smart_slice(
        corpus=corpus,
        queries=queries,
        qrels=qrels,
        max_queries=2,
        distractor_ratio=2.0,
        seed=42,
    )
    slice_ids = {d["id"] for d in slice_corpus}
    # Every relevant doc must land in the slice — non-negotiable
    assert "doc_rel_1" in slice_ids
    assert "doc_rel_2" in slice_ids
    # Total = 2 must_have + 4 distractors (capped by available pool)
    assert len(slice_corpus) == 6
    assert len(slice_queries) == 2


def test_smart_slice_caps_max_queries() -> None:
    """``max_queries`` must clamp the query count when the dataset has
    more queries than the cap."""
    corpus = [{"id": f"d{i}", "title": "x", "text": "x"} for i in range(20)]
    queries = [{"id": f"q{i}", "text": "q"} for i in range(20)]
    qrels = {f"q{i}": {f"d{i}": 1} for i in range(20)}
    _, slice_queries = _mb._pick_smart_slice(
        corpus=corpus,
        queries=queries,
        qrels=qrels,
        max_queries=5,
    )
    assert len(slice_queries) == 5
