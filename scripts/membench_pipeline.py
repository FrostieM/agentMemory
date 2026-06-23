"""scripts/membench_pipeline.py — pipeline-level BEIR retrieval benchmark.

Companion to ``membench.py`` (internal sanity) and
``membench_external.py`` (embedder vs published numbers). Where the
external script runs ``sentence-transformers`` directly against BEIR,
this one drives the WHOLE retrieval stack — ingestion + chunking +
FTS5 + LanceDB + cross-encoder reranker + 7 brain loops — against the same
BEIR task so the operator sees whether our pipeline ADDS quality
over the bare embedder or NOISE that should be tuned out.

Isolation warning: by default the ingested corpus would land in the
host workspace's DB (whatever the running service is anchored to).
Use ``--db-path`` to point the script at a throwaway DB file so the
host workspace stays clean — recommended for CI / repeated runs.
A v3.5 incident on agent-memory-lite proved this matters: 197 BEIR
docs leaked into the project DB before being cleaned up.

How it differs from the embedder-only run:

* External script: encodes 5k corpus docs + 300 queries via
  sentence-transformers ONLY, computes cosine similarities, returns
  NDCG@10. Result on SciFact (2026-05-20): 0.6694, ~at published
  baseline for ``intfloat/multilingual-e5-small``.
* This script: ingests the same 5k docs through
  ``/memory/ingest_episode`` (going through redaction, chunking,
  embedding, FTS5, audit), then queries each BEIR question via
  ``/memory/search`` (per-kind FTS5 BM25 + LIKE fallback, optional
  cross-encoder rerank). Returns NDCG@10 / Recall@10 / MAP@10 over
  the same qrels. Comparable apples-to-apples.

Runtime: ingestion is the long pole. ~3 sec per doc through HTTP
(embed + FTS + audit), so 5k docs = ~4 hours on CPU. Use
``--max-docs N`` for a short sanity slice (500 docs ~25 min);
``--queries N`` to cap evaluated queries.

CLI::

    python scripts/membench_pipeline.py --task SciFact --max-docs 500
    python scripts/membench_pipeline.py --task SciFact --json --save report.json

The script creates / tears down a workspace named
``beir_<task>_pipeline`` so the host workspace is never polluted.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from pathlib import Path

from agent_memory_lite.evals.retrieval_regression import compare_metrics

# Windows cp1251 stdout chokes on em-dash + right-arrow. Force UTF-8 so
# the report renders the same on every terminal.
with contextlib.suppress(AttributeError, ValueError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
with contextlib.suppress(AttributeError, ValueError):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

DEFAULT_BASE_URL = "http://127.0.0.1:8765"
_TIMEOUT_INGEST = 30.0
_TIMEOUT_SEARCH = 15.0
_BATCH_LOG_EVERY = 100  # progress every N ingestions


def _http_post(
    url: str,
    payload: dict[str, object],
    *,
    timeout: float,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, object] | None:
    """Best-effort POST. Returns parsed JSON dict or None on any error."""
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _ingest_doc(
    *,
    base_url: str,
    workspace_id: str,
    beir_doc_id: str,
    title: str,
    text: str,
    db_path: str | None = None,
    vector_path: str | None = None,
) -> str | None:
    """Ingest one BEIR doc as an episode tagged with the original
    beir_doc_id so we can recover ground-truth membership later.
    Returns the episode_id on success, None on failure.

    When ``db_path`` is provided, sends the ``X-Memory-DB-Path`` /
    ``X-Memory-Vector-Path`` headers so the service routes the write
    to a throwaway file instead of polluting the host workspace.
    """
    payload = {
        "workspace_id": workspace_id,
        "raw_text": f"{title}\n\n{text}",
        "source_type": "file_indexed",
        "trust_level": "agent_observed",
        "importance": 0.5,
        "metadata": {"beir_doc_id": beir_doc_id, "beir_title": title[:200]},
    }
    headers: dict[str, str] = {}
    if db_path:
        headers["X-Memory-DB-Path"] = db_path
    if vector_path:
        headers["X-Memory-Vector-Path"] = vector_path
    data = _http_post(
        f"{base_url}/memory/ingest_episode",
        payload,
        timeout=_TIMEOUT_INGEST,
        extra_headers=headers,
    )
    if data is None:
        return None
    eid = data.get("episode_id")
    return str(eid) if isinstance(eid, str) else None


def _search(
    *,
    base_url: str,
    workspace_id: str,
    query: str,
    k: int,
    db_path: str | None = None,
    vector_path: str | None = None,
) -> list[dict[str, object]]:
    """Query /memory/search. Returns the raw hits list (may include
    metadata where the BEIR doc id lives)."""
    payload = {"workspace_id": workspace_id, "query": query, "limit": k}
    headers: dict[str, str] = {}
    if db_path:
        headers["X-Memory-DB-Path"] = db_path
    if vector_path:
        headers["X-Memory-Vector-Path"] = vector_path
    data = _http_post(
        f"{base_url}/memory/search",
        payload,
        timeout=_TIMEOUT_SEARCH,
        extra_headers=headers,
    )
    if data is None:
        return []
    # memory_search returns {ok, data: list, ...} for MCP-shape calls and
    # {hits: [...]} or {results: [...]} for HTTP variants. Accept either.
    if isinstance(data.get("hits"), list):
        return [h for h in data["hits"] if isinstance(h, dict)]
    if isinstance(data.get("results"), list):
        return [h for h in data["results"] if isinstance(h, dict)]
    if isinstance(data.get("data"), list):
        return [h for h in data["data"] if isinstance(h, dict)]
    return []


def _extract_beir_id(hit: dict[str, object]) -> str | None:
    """Walk a hit looking for the original BEIR doc id we stamped at
    ingestion time. metadata may be nested via 'metadata' or
    'episode_metadata' depending on the surface; we just look anywhere
    that looks like JSON-shaped metadata."""
    for key in ("metadata", "episode_metadata", "meta"):
        m = hit.get(key)
        if isinstance(m, dict):
            v = m.get("beir_doc_id")
            if isinstance(v, str):
                return v
            if isinstance(v, (int, float)):
                return str(v)
    # Final fallback: hit body sometimes carries the title we stored,
    # which contains the beir id implicitly. Not enough for a match,
    # but lets the caller debug a missed hit.
    return None


def _ndcg_at_k(rels: list[int], k: int) -> float:
    """Standard NDCG@k for binary relevance. `rels` is the relevance of
    each returned doc, in returned order (1 if BEIR qrels marks
    relevant, 0 otherwise)."""
    dcg = 0.0
    for i, r in enumerate(rels[:k], start=1):
        if r > 0:
            dcg += r / math.log2(i + 1)
    ideal = sorted(rels, reverse=True)[:k]
    idcg = sum(r / math.log2(i + 1) for i, r in enumerate(ideal, start=1) if r > 0)
    return (dcg / idcg) if idcg > 0 else 0.0


def _recall_at_k(rels: list[int], total_relevant: int, k: int) -> float:
    if total_relevant <= 0:
        return 0.0
    hits = sum(1 for r in rels[:k] if r > 0)
    return hits / total_relevant


def _ap(rels: list[int]) -> float:
    """Average precision over the full returned list."""
    if not any(rels):
        return 0.0
    hits = 0
    summed = 0.0
    for i, r in enumerate(rels, start=1):
        if r > 0:
            hits += 1
            summed += hits / i
    return summed / hits if hits else 0.0


def _pick_smart_slice(
    *,
    corpus: Iterable[dict[str, object]],
    queries: Iterable[dict[str, object]],
    qrels: dict[str, dict[str, int]],
    max_queries: int,
    distractor_ratio: float = 5.0,
    seed: int = 42,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Return (slice_corpus, slice_queries) where every query's
    relevant_docs are in the slice + a controlled number of
    distractors so the benchmark actually measures discriminative
    ranking instead of "is the doc even in our DB".

    Naïve ``corpus[:N]`` slicing loses 95% of queries because BEIR
    qrels are sparse across the 5k-doc corpus — see the 20-doc
    smoke test that scored 0/10. Smart slicing guarantees coverage.
    """
    rnd = random.Random(seed)
    query_list = list(queries)[:max_queries]
    needed_doc_ids: set[str] = set()
    for q in query_list:
        rel = qrels.get(str(q["id"])) or {}
        needed_doc_ids.update(rel.keys())
    by_id = {str(d["id"]): d for d in corpus}
    must_have = [by_id[did] for did in needed_doc_ids if did in by_id]
    # Distractors: shuffle the rest, take ratio * |must_have|.
    remaining_ids = [did for did in by_id if did not in needed_doc_ids]
    rnd.shuffle(remaining_ids)
    distractor_count = min(int(len(must_have) * distractor_ratio), len(remaining_ids))
    distractors = [by_id[did] for did in remaining_ids[:distractor_count]]
    slice_corpus = must_have + distractors
    rnd.shuffle(slice_corpus)
    return slice_corpus, query_list


def run_pipeline_benchmark(  # noqa: PLR0915 — linear ingest→search→score pipeline; splitting hurts readability
    *,
    task: str,
    base_url: str,
    workspace_id: str,
    max_docs: int | None,
    max_queries: int | None,
    smart_slice: bool = True,
    k: int = 10,
    db_path: str | None = None,
    vector_path: str | None = None,
) -> dict[str, float]:
    """Ingest corpus, run queries, return metrics dict. Heavy
    deps imported lazily so the script imports clean without mteb.

    Set ``smart_slice=True`` (default) to bypass the naïve
    ``corpus[:max_docs]`` trim and instead include the docs that
    actually answer the chosen queries + a 5x distractor ratio.
    Lets a 200-doc slice produce a meaningful number; the naïve
    slice would score zero because qrels are sparse."""
    import mteb  # noqa: PLC0415

    tasks = mteb.get_tasks(tasks=[task])
    mteb_task = tasks[0]
    mteb_task.load_data()
    test = mteb_task.dataset["default"]["test"]
    corpus = test["corpus"]
    queries = test["queries"]
    qrels = test["relevant_docs"]

    if smart_slice and max_queries is not None:
        docs, queries_used = _pick_smart_slice(
            corpus=corpus,
            queries=queries,
            qrels=qrels,
            max_queries=max_queries,
        )
    else:
        corpus_iter: Iterable[dict[str, object]] = corpus
        if max_docs is not None:
            corpus_iter = list(corpus)[:max_docs]
        docs = list(corpus_iter)
        queries_used = list(queries)
        if max_queries is not None:
            queries_used = queries_used[:max_queries]
    print(f"membench_pipeline: ingesting {len(docs)} corpus docs ...", file=sys.stderr)
    start = time.time()
    failed = 0
    for i, doc in enumerate(docs, start=1):
        eid = _ingest_doc(
            base_url=base_url,
            workspace_id=workspace_id,
            beir_doc_id=str(doc["id"]),
            title=str(doc.get("title", "")),
            text=str(doc.get("text", "")),
            db_path=db_path,
            vector_path=vector_path,
        )
        if eid is None:
            failed += 1
        if i % _BATCH_LOG_EVERY == 0:
            elapsed = time.time() - start
            print(
                f"  {i}/{len(docs)} ingested (failed={failed}, "
                f"{elapsed:.0f}s, {i / max(elapsed, 0.001):.1f}/s)",
                file=sys.stderr,
            )
    ingest_elapsed = time.time() - start
    print(
        f"membench_pipeline: ingest done in {ingest_elapsed:.0f}s, failed={failed}/{len(docs)}",
        file=sys.stderr,
    )

    query_list = queries_used
    # Restrict qrels to queries for which the relevant doc was actually
    # ingested in this slice — otherwise NDCG is artificially zero for
    # queries whose answer wasn't in our subset.
    ingested_ids = {str(d["id"]) for d in docs}

    ndcgs: list[float] = []
    recalls: list[float] = []
    aps: list[float] = []
    queries_scored = 0
    start = time.time()
    for q in query_list:
        qid = str(q["id"])
        rel_map = qrels.get(qid) or {}
        rel_in_slice = {
            doc_id: score for doc_id, score in rel_map.items() if doc_id in ingested_ids
        }
        if not rel_in_slice:
            continue
        hits = _search(
            base_url=base_url,
            workspace_id=workspace_id,
            query=str(q["text"]),
            k=k,
            db_path=db_path,
            vector_path=vector_path,
        )
        # Build the rels array in returned-rank order.
        rels: list[int] = []
        for hit in hits:
            beir_id = _extract_beir_id(hit)
            if beir_id is None:
                rels.append(0)
                continue
            rels.append(1 if beir_id in rel_in_slice else 0)
        ndcgs.append(_ndcg_at_k(rels, k))
        recalls.append(_recall_at_k(rels, len(rel_in_slice), k))
        aps.append(_ap(rels))
        queries_scored += 1
    query_elapsed = time.time() - start
    print(
        f"membench_pipeline: queries done in {query_elapsed:.0f}s, "
        f"evaluated={queries_scored}/{len(query_list)}",
        file=sys.stderr,
    )

    return {
        "ndcg_at_10": (sum(ndcgs) / len(ndcgs)) if ndcgs else 0.0,
        "recall_at_10": (sum(recalls) / len(recalls)) if recalls else 0.0,
        "map_at_10": (sum(aps) / len(aps)) if aps else 0.0,
        "queries_evaluated": float(queries_scored),
        "docs_ingested": float(len(docs) - failed),
    }


def _format_summary(task: str, scores: dict[str, float], embedder_score: float) -> str:
    lines = [
        "",
        f"membench_pipeline — BEIR {task} (full stack: ingest → FTS5 + rerank → search)",
        "=" * 72,
        f"  queries evaluated: {int(scores['queries_evaluated'])}",
        f"  docs in slice    : {int(scores['docs_ingested'])}",
        "",
        f"  NDCG@10  : {scores['ndcg_at_10']:.4f}",
        f"  Recall@10: {scores['recall_at_10']:.4f}",
        f"  MAP@10   : {scores['map_at_10']:.4f}",
        "",
        "  Comparison with embedder-only run (membench_external):",
        f"    embedder only          : {embedder_score:.4f}",
        f"    our full stack         : {scores['ndcg_at_10']:.4f}",
        f"    delta (stack - embed)  : {scores['ndcg_at_10'] - embedder_score:+.4f}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MemBench pipeline — full-stack BEIR benchmark (ingest + search)."
    )
    parser.add_argument("--task", default="SciFact")
    parser.add_argument(
        "--max-docs",
        type=int,
        default=None,
        help="Cap on corpus docs (naive slice). Ignored when --smart-slice "
        "is set (the default); see --queries for sizing the smart slice.",
    )
    parser.add_argument(
        "--queries",
        type=int,
        default=30,
        help="Evaluate this many queries. With smart-slice (default) this "
        "also sizes the corpus: every query's relevant docs ingested + "
        "5x distractors. Lower for quick runs, raise for fuller coverage.",
    )
    parser.add_argument(
        "--no-smart-slice",
        action="store_true",
        help="Disable smart slicing; use naive --max-docs prefix instead.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace id to use (auto-generated when omitted).",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Throwaway SQLite path for ingestion via X-Memory-DB-Path. "
        "Required to keep BEIR docs out of the host workspace DB.",
    )
    parser.add_argument(
        "--vector-path",
        default=None,
        help="Throwaway LanceDB dir paired with --db-path.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--save", type=Path)
    parser.add_argument(
        "--compare-baseline",
        type=Path,
        help="Compare NDCG@10/Recall@10/MAP@10 against a saved baseline JSON and "
        "exit 1 if NDCG@10 drops by more than 5%%. The fixed BEIR corpus makes "
        "this baseline stable across runs (unlike membench.py's mutable-decision "
        "baseline), so it is the one suitable for a scheduled regression gate.",
    )
    parser.add_argument(
        "--embedder-baseline",
        type=float,
        default=0.6694,
        help="NDCG@10 from membench_external (default = our 2026-05-20 SciFact run).",
    )
    args = parser.parse_args()

    workspace_id = args.workspace or f"beir_{args.task.lower()}_pipeline"
    if not args.db_path:
        print(
            "membench_pipeline: warning — no --db-path supplied. BEIR docs "
            "will be ingested into the host workspace DB. Pass --db-path "
            "to keep the host workspace clean.",
            file=sys.stderr,
        )
    scores = run_pipeline_benchmark(
        task=args.task,
        base_url=args.base_url,
        workspace_id=workspace_id,
        max_docs=args.max_docs,
        max_queries=args.queries,
        smart_slice=not args.no_smart_slice,
        db_path=args.db_path,
        vector_path=args.vector_path,
    )
    payload: dict[str, object] = {"task": args.task, "workspace_id": workspace_id, **scores}
    exit_code = 0
    deltas: dict[str, float] = {}
    if args.compare_baseline:
        try:
            prev = json.loads(args.compare_baseline.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev = None
        if isinstance(prev, dict):
            result = compare_metrics(
                payload,
                prev,
                metrics=["ndcg_at_10", "recall_at_10", "map_at_10"],
                primary="ndcg_at_10",
            )
            deltas = result.deltas
            exit_code = result.exit_code
            payload["baseline_diff"] = deltas
    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(_format_summary(args.task, scores, args.embedder_baseline))
        if deltas:
            print("  vs baseline:")
            for metric_name, metric_delta in deltas.items():
                sign = "+" if metric_delta >= 0 else ""
                print(f"    {metric_name:14s} {sign}{metric_delta:.4f}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
