"""Static HTML page routes for the dashboard.

Phase 3.3 of v2.2 consolidation. Split out of ``api/routes/ui.py`` once
the review page pushed the host module past the ≤150-SLOC ceiling.

v3.0.0-final added three brain-aware pages: ``/ui/recall`` (interactive
spreading activation), ``/ui/reflexes`` (PreToolUse rule CRUD), and
``/ui/metrics`` (brain telemetry dashboard). The shared ``brain.js``
module backs all three plus the Observatory rail cards.

This module owns every HTML landing page + the catch-all
``/ui/{asset_name}`` that serves the JS / CSS assets registered in
``_ASSETS``. SSE + state-API routes stay in the original ui.py since
they have very different lifecycles (long-lived streams vs one-shot
file responses).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(include_in_schema=False)

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_UI_ROOT = _PACKAGE_ROOT / "ui"

_ASSETS: dict[str, str] = {
    "app.js": "application/javascript; charset=utf-8",
    # 2.2 (Phase 2.3): shared header script for /ui/code and /ui/graph.
    "app_header.js": "application/javascript; charset=utf-8",
    # v3.0.0-final: shared brain-render module (self-model card,
    # outcome pills, watch-outs, recent insights, recall rendering).
    "brain.js": "application/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "code.html": "text/html; charset=utf-8",
    "graph.html": "text/html; charset=utf-8",
    # 2.2 (Phase 3.3): candidate review queue with promote/reject UI.
    "review.html": "text/html; charset=utf-8",
    # 2.2 (Phase 3.6): generic browse page over decisions / theories /
    # insights / behavior_instructions with kind selector.
    "browse.html": "text/html; charset=utf-8",
    # v3.0.0-final brain pages.
    "recall.html": "text/html; charset=utf-8",
    "reflexes.html": "text/html; charset=utf-8",
    "metrics.html": "text/html; charset=utf-8",
}

_NO_CACHE = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _serve_html(filename: str) -> FileResponse:
    return FileResponse(
        _UI_ROOT / filename,
        media_type="text/html; charset=utf-8",
        headers=_NO_CACHE,
    )


@router.get("/ui")
def memory_ui_index() -> FileResponse:
    return _serve_html("index.html")


@router.get("/ui/code")
def memory_ui_code() -> FileResponse:
    """2.0 dashboard backed by /memory/code_overview."""
    return _serve_html("code.html")


@router.get("/ui/graph")
def memory_ui_graph() -> FileResponse:
    """2.1.2 D3 graph dashboard backed by /memory/code_graph."""
    return _serve_html("graph.html")


@router.get("/ui/review")
def memory_ui_review() -> FileResponse:
    """2.2 (Phase 3.3) candidate review page backed by /memory/review_queue."""
    return _serve_html("review.html")


@router.get("/ui/browse")
def memory_ui_browse() -> FileResponse:
    """2.2 (Phase 3.6) generic browse over decisions / theories / insights / behaviors."""
    return _serve_html("browse.html")


@router.get("/ui/recall")
def memory_ui_recall() -> FileResponse:
    """v3.0.0-final interactive spreading-activation explorer."""
    return _serve_html("recall.html")


@router.get("/ui/reflexes")
def memory_ui_reflexes() -> FileResponse:
    """v3.0.0-final reflex-rule CRUD dashboard."""
    return _serve_html("reflexes.html")


@router.get("/ui/metrics")
def memory_ui_metrics() -> FileResponse:
    """v3.0.0-final brain telemetry dashboard."""
    return _serve_html("metrics.html")


@router.get("/ui/{asset_name}")
def memory_ui_asset(asset_name: str) -> FileResponse:
    """Serve a registered asset; unknown names fall back to index.html."""
    if asset_name not in _ASSETS:
        return FileResponse(
            _UI_ROOT / "index.html",
            media_type="text/html; charset=utf-8",
            headers=_NO_CACHE,
        )
    return FileResponse(
        _UI_ROOT / asset_name,
        media_type=_ASSETS[asset_name],
        headers=_NO_CACHE,
    )
