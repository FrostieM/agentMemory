"""scripts/membench_external.py — cross-project retrieval benchmark via BEIR.

Companion to ``scripts/membench.py`` which measures internal sanity
(can we find our own decisions). This one measures our embedding
component against PUBLIC benchmarks so the operator can compare with
other retrieval systems on equal footing.

What it does: loads a small BEIR task via the ``mteb`` library,
encodes corpus + queries with our deployed ``EMBEDDING_MODEL``
(default ``intfloat/multilingual-e5-small``), computes NDCG@10 +
Recall@10 + MAP@10 - the BEIR standard metrics - and prints them
next to **published** scores for comparable systems so the operator
sees where we stand without leaving the terminal.

Caveats stated up front:

* This measures the **embedding component** only — corpus + queries
  go through ``sentence-transformers`` directly. The retrieval
  pipeline used by ``/memory/search`` (FTS5 + LIKE fallback + optional
  reranker + 7 brain loops) is NOT exercised. A pipeline-level external benchmark would
  require ingesting the BEIR corpus into our memory (5k+ documents,
  hours of CPU time) - out of scope for this MVP.
* Defaults to BEIR ``SciFact`` because it is the smallest standard
  task (~5k docs, 300 queries) and runs in 5 to 10 min on CPU. Larger
  tasks land via ``--task TaskName``.
* MTEB downloads the dataset on first run to its default cache
  (``~/.cache/mteb/`` or ``HF_DATASETS_CACHE``). One-time ~5MB.

Published scores for comparison (from MTEB leaderboard, snapshot
2026-05; numbers are NDCG@10 on BEIR SciFact unless noted):

    BM25 baseline                    ~67  (no embeddings, lexical only)
    intfloat/multilingual-e5-small   ~68  (our model)
    OpenAI text-embedding-ada-002    ~72
    Cohere embed-multilingual-v3.0   ~74
    nomic-embed-text-v1              ~73

If our run lands in ``66 to 70`` range, the embedding component is
working as advertised. Lower than ``65`` would suggest a config bug
or model download corruption; higher than ``70`` would be unexpected.

CLI::

    python scripts/membench_external.py
    python scripts/membench_external.py --task NFCorpus --json
    python scripts/membench_external.py --task SciFact --save report.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Pinned reference scores from the published MTEB leaderboard — used so the
# operator sees the field at the same time as the result. Update from
# https://huggingface.co/spaces/mteb/leaderboard when the snapshot drifts.
_REFERENCE_NDCG10 = {
    "SciFact": {
        "BM25 baseline": 0.67,
        "intfloat/multilingual-e5-small (published)": 0.68,
        "OpenAI text-embedding-ada-002": 0.72,
        "Cohere embed-multilingual-v3.0": 0.74,
        "nomic-embed-text-v1": 0.73,
    },
    "NFCorpus": {
        "BM25 baseline": 0.33,
        "intfloat/multilingual-e5-small (published)": 0.32,
        "OpenAI text-embedding-ada-002": 0.36,
        "Cohere embed-multilingual-v3.0": 0.38,
    },
}


def _format_summary(task: str, our_scores: dict[str, float]) -> str:
    """Plain-text comparison table: our number next to published ones."""
    refs = _REFERENCE_NDCG10.get(task, {})
    our = our_scores.get("ndcg_at_10", 0.0)
    lines = [
        "",
        f"membench_external — BEIR {task}",
        "=" * 64,
        "  Our deployment (intfloat/multilingual-e5-small via local stack):",
        f"    NDCG@10  : {our:.4f}",
        f"    Recall@10: {our_scores.get('recall_at_10', 0.0):.4f}",
        f"    MAP@10   : {our_scores.get('map_at_10', 0.0):.4f}",
        "",
        "  Published reference NDCG@10 (MTEB leaderboard):",
    ]
    for name, score in refs.items():
        marker = "<- our model" if "e5-small" in name else ""
        delta = ""
        if "e5-small (published)" in name:
            d = our - score
            delta = f"  (delta from published: {d:+.4f})"
        lines.append(f"    {score:.2f}  {name}{marker}{delta}")
    return "\n".join(lines) + "\n"


def run_external_benchmark(
    *,
    task: str = "SciFact",
    model_name: str = "intfloat/multilingual-e5-small",
    output_folder: str | None = None,
) -> dict[str, float]:
    """Run one BEIR task via mteb. Returns flat dict with NDCG@10,
    Recall@10, MAP@10. Lazy-imports so module is importable without
    mteb installed (tests can skip)."""
    import mteb  # noqa: PLC0415
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415

    model = SentenceTransformer(model_name)
    tasks = mteb.get_tasks(tasks=[task])
    evaluation = mteb.MTEB(tasks=tasks)
    if output_folder is None:
        output_folder = str(Path.home() / ".cache" / "membench_external" / task)
    raw = evaluation.run(model, output_folder=output_folder, verbosity=1)
    # mteb returns list[TaskResult]; pull the first task's test split
    # scores (the canonical evaluation split for BEIR).
    if not raw:
        return {"ndcg_at_10": 0.0, "recall_at_10": 0.0, "map_at_10": 0.0}
    result = raw[0]
    # Result shape: result.scores is dict[split, list[dict[lang, metric_dict]]]
    # For BEIR tasks the split is "test" and lang is "default" (or actual
    # language code). We just walk and pull the first one.
    scores: dict[str, float] = {}
    try:
        split_results = result.scores.get("test") or next(iter(result.scores.values()))
        first = split_results[0] if isinstance(split_results, list) else split_results
        if isinstance(first, dict):
            for k in ("ndcg_at_10", "recall_at_10", "map_at_10"):
                v = first.get(k)
                if isinstance(v, (int, float)):
                    scores[k] = float(v)
    except (AttributeError, IndexError, KeyError, StopIteration):
        pass
    return scores or {"ndcg_at_10": 0.0, "recall_at_10": 0.0, "map_at_10": 0.0}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MemBench external — BEIR retrieval benchmark cross-comparison."
    )
    parser.add_argument(
        "--task",
        default="SciFact",
        help=f"BEIR task name. Known small ones: {list(_REFERENCE_NDCG10.keys())}",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("EMBEDDING_MODEL", "intfloat/multilingual-e5-small"),
        help="HuggingFace sentence-transformers model id.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--save",
        type=Path,
        help="After running, write the score JSON to this path.",
    )
    args = parser.parse_args()
    try:
        scores = run_external_benchmark(task=args.task, model_name=args.model)
    except ImportError:
        print(
            "membench_external: install mteb + datasets first:\n"
            "    .venv/Scripts/python.exe -m pip install mteb",
            file=sys.stderr,
        )
        return 2
    payload = {"task": args.task, "model": args.model, **scores}
    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(_format_summary(args.task, scores))
    return 0


if __name__ == "__main__":
    sys.exit(main())
