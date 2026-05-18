"""Cross-encoder reranker — opt-in refinement after RRF fusion.

Re-orders the top-N hits from canonical search by query-document semantic
relevance using ``jina-reranker-v1-tiny-en`` (~33 MB, CPU-friendly).
The model is *not* a required dep:

* Install with ``pip install 'agent-memory-lite[rerank]'`` to enable.
* Without the extra, ``rerank_hits()`` returns the input list
  unchanged and ``is_available()`` returns False.

Design constraints:

* **Lazy load.** The model is loaded the first time ``rerank_hits``
  is called, not at import. Cold-start of the model is ~1.5s; the
  HTTP service keeps it warm thereafter. ``memory-cli`` skips
  reranking by default (sub-process cold start is a worst case).
* **Failure-soft.** Any error during loading or scoring falls back
  to the original RRF order — never raises.
* **No PyTorch import on import.** The reranker module is safe to
  import in environments without torch. We touch torch only inside
  ``_load_model``.

Plug-in point: ``rerank_hits(query, hits, top_k=10)`` accepts the
``SearchHit`` list (or any object with ``.projection`` dict and
``.kind``) and returns a re-scored, re-ordered list of the same
length, capped to ``top_k``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger("agent_memory_lite.rerank")

DEFAULT_MODEL = "jinaai/jina-reranker-v1-tiny-en"


# ============================================================
# Hit protocol — accept SearchHit or any duck-typed equivalent
# ============================================================


class RerankableHit(Protocol):
    """Minimal shape needed from a hit to rerank it."""

    kind: str
    projection: dict[str, Any]
    score: float


@dataclass(frozen=True, slots=True)
class _ScoredHit:
    """Internal — pairs an original hit with its cross-encoder score."""

    hit: Any
    score: float


# ============================================================
# Availability + model loader
# ============================================================


_model_cache: dict[str, Any] = {}


def is_available() -> bool:
    """Return True iff ``sentence-transformers`` can be imported.

    Cheap check — runs every call, but the import is cached by Python
    so the second call is sub-microsecond. Tests use this to skip
    cleanly when the optional dep is missing.
    """
    try:
        import sentence_transformers  # noqa: F401, PLC0415 — optional probe
    except ImportError:
        return False
    return True


def _load_model(model_name: str) -> Any:
    """Lazy-load + cache the CrossEncoder model. Returns None on failure."""
    cached = _model_cache.get(model_name)
    if cached is not None:
        return cached
    try:
        from sentence_transformers import CrossEncoder  # noqa: PLC0415 — optional dep
    except ImportError:
        logger.info("rerank_dep_missing", extra={"model": model_name})
        return None
    try:
        model = CrossEncoder(model_name)
    except Exception as exc:  # model load may fail many ways
        logger.warning(
            "rerank_model_load_failed",
            extra={"model": model_name, "error": str(exc)},
        )
        return None
    _model_cache[model_name] = model
    return model


# ============================================================
# Hit → text snippet for scoring
# ============================================================


def _snippet_for(hit: RerankableHit) -> str:
    """Build a short text representation of a hit for the cross-encoder.

    Concatenates the most signal-dense fields per kind. Falls back to
    str(projection) when the kind is unknown.
    """
    projection = hit.projection or {}
    kind = hit.kind or ""
    title = str(projection.get("title") or projection.get("name") or "")
    gist = str(projection.get("gist") or projection.get("rule_one_line") or "")
    if kind == "code_digest":
        title = str(projection.get("file_path") or "")
        gist = str(projection.get("purpose_short") or "")
    snippet = f"{title}. {gist}".strip(" .")
    if snippet:
        return snippet[:512]
    return str(projection)[:512]


# ============================================================
# Public entrypoint
# ============================================================


def rerank_hits(
    query: str,
    hits: list[Any],
    *,
    top_k: int = 10,
    model_name: str = DEFAULT_MODEL,
) -> list[Any]:
    """Return the top-``top_k`` hits re-ordered by cross-encoder score.

    Failure-soft contract:

    * ``hits`` empty → return empty list.
    * Model unavailable (extra not installed or load failed) → return
      the first ``top_k`` of the original list unchanged.
    * Any exception during scoring → log and return the first ``top_k``
      of the original list unchanged.
    """
    if not hits:
        return []
    capped = hits[: max(top_k, 0) * 5 or top_k]  # cap input at 5x top_k for speed
    model = _load_model(model_name)
    if model is None:
        return capped[:top_k]
    try:
        pairs = [(query, _snippet_for(h)) for h in capped]
        scores = model.predict(pairs)
    except Exception as exc:  # never let reranker bring down search
        logger.warning(
            "rerank_predict_failed",
            extra={"model": model_name, "error": str(exc)},
        )
        return capped[:top_k]
    scored = [_ScoredHit(hit=h, score=float(s)) for h, s in zip(capped, scores, strict=False)]
    scored.sort(key=lambda x: x.score, reverse=True)
    return [s.hit for s in scored[:top_k]]
