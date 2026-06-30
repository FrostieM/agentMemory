"""In-process, offline golden-set retrieval runner for the MRR/NDCG@10 gate.

Seeds a FIXED corpus of durable memory rows into a fresh in-memory DB ONCE, then
runs ``search()`` for each labelled query and computes recall@10 / MRR@10 /
NDCG@10 (binary relevance) over the SHARED pool -- so each query's relevant rows
compete against the whole corpus (real ranking, real distractors), unlike the
per-case throwaway seeding of the existing run_evals retrieval cases.

``search`` is called with an EXPLICIT ``kinds`` list (the four corpus kinds), so
each kind gets the full ``limit`` budget (#120) and the top-10 is a genuine
cross-kind merge over the full per-kind candidate pools -- not the default
``kinds=None`` path, which divides ``limit`` across all 13 kinds (max(2, 10//13)
= top-2 per kind) and would window each query down to ~8 rows. The RANKING logic
(durable FTS5/BM25 score, the score squash, the global merge-sort, rerank) is
identical to the default ``memory_search`` path; only the per-kind budget
differs, so a real ranking regression still moves the metrics here.

The corpus mixes EASY clusters (each query's answer is the dominant lexical match)
with HARD adversarial sets: a query whose relevant row wins on a RARE term (high
BM25/IDF) while a same-kind distractor packs MORE of the query's COMMON tokens
(so the naive LIKE token-overlap fallback ranks the distractor first). Those sets
give the gate teeth: if the FTS5/BM25 arm silently breaks and search falls back
to the LIKE path, MRR/NDCG drop well past the regression threshold.

FTS-only: no embedding model, no HTTP service, no ``mteb`` -- fully deterministic
and CI-runnable (the existing membench/membench_pipeline benches need a live
service or mteb, so they cannot gate CI). The result dict feeds
``evals.retrieval_regression.compare_metrics`` against a committed baseline.
"""

from __future__ import annotations

import sqlite3
from importlib import resources
from typing import Any

import yaml  # type: ignore[import-untyped]

from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.evals.metrics import ndcg_at_k, recall_at_k, reciprocal_rank
from agent_memory_lite.ingestion.canonical_writer import write_canonical
from agent_memory_lite.storage.reader import search

_WS = "golden"
_GOLDEN_PACKAGE = "agent_memory_lite.evals.golden"
# The kinds present in the fixture. Passed EXPLICITLY to search() so each gets the
# full per-kind budget (whole-corpus cross-kind ranking) instead of the default
# kinds=None divided budget. Keep in sync with the kinds used in golden_set.yaml.
_CORPUS_KINDS = ["decision", "theory", "concept", "insight"]


def load_golden_set() -> dict[str, list[dict[str, Any]]]:
    """Load the committed corpus + queries fixture (ships with the package)."""
    text = resources.files(_GOLDEN_PACKAGE).joinpath("golden_set.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    return {"corpus": list(data.get("corpus") or []), "queries": list(data.get("queries") or [])}


def _seed_corpus(conn: sqlite3.Connection, corpus: list[dict[str, Any]]) -> dict[str, str]:
    """Write every corpus row via write_canonical; return {label -> row id}."""
    label_to_id: dict[str, str] = {}
    for row in corpus:
        payload = dict(row.get("payload") or {})
        out = write_canonical(conn, workspace_id=_WS, kind=str(row["kind"]), payload=payload)
        if out is None:
            raise ValueError(
                f"golden corpus row {row.get('label')!r} ({row.get('kind')}) failed to write"
            )
        label_to_id[str(row["label"])] = str(out["id"])
    return label_to_id


def run_golden_eval(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Seed the corpus, run every query, return aggregate recall/MRR/NDCG@10."""
    data = load_golden_set()
    # global-audit round-A: fail fast on an empty query set. Previously
    # ``n = len(queries) or 1`` divided by 1, reporting all-0.0 metrics for a
    # corrupted/empty fixture -- and a 0.0-vs-0.0 comparison passes the regression
    # gate (delta 0.0), silently laundering a broken golden set.
    if not data.get("queries"):
        raise ValueError(
            "golden set has zero queries -- refusing to emit 0.0 metrics that would "
            "silently pass the retrieval-regression gate."
        )
    own = conn is None
    if conn is None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        apply_migrations(conn)
    try:
        label_to_id = _seed_corpus(conn, data["corpus"])
        recalls: list[float] = []
        mrrs: list[float] = []
        ndcgs: list[float] = []
        for query in data["queries"]:
            relevant = query.get("relevant", [])
            # round-A: a query with NO declared relevant labels scores a silent
            # perfect 1.0 (recall/MRR/NDCG return 1.0 on an empty expected set),
            # masking a malformed fixture. Every golden query must assert relevance.
            if not relevant:
                raise ValueError(
                    f"golden query {query.get('query')!r} declares no 'relevant' labels"
                )
            expected = [label_to_id[label] for label in relevant]
            hits = search(
                conn,
                workspace_id=_WS,
                query=str(query["query"]),
                kinds=_CORPUS_KINDS,
                limit=10,
                log_coactivations=False,
            )
            retrieved = [str(h.projection["id"]) for h in hits]
            recalls.append(recall_at_k(retrieved, expected, k=10))
            mrrs.append(reciprocal_rank(retrieved, expected, k=10))
            ndcgs.append(ndcg_at_k(retrieved, expected, k=10))
        n = len(data["queries"])  # guaranteed >= 1 by the guard above
        return {
            "queries_evaluated": len(data["queries"]),
            "corpus_size": len(data["corpus"]),
            "retrieval_recall_at_10": round(sum(recalls) / n, 4),
            "retrieval_mrr_at_10": round(sum(mrrs) / n, 4),
            "retrieval_ndcg_at_10": round(sum(ndcgs) / n, 4),
        }
    finally:
        if own:
            conn.close()
