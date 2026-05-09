"""Vendor-asset routes for the /ui dashboard.

Phase 2.7 of v2.2 consolidation: split out of ``api/routes/ui.py`` to
honour the ≤150-SLOC ceiling. The vendor route serves third-party JS
bundles (currently just D3) that the dashboard pages reference via bare
same-origin ``<script src="/ui/vendor/...">`` tags. Because the bundle
is same-origin the browser does not require an SRI integrity hash, and
we no longer depend on a CDN at runtime — this restores the local-only
philosophy from the project's hard rules (see CLAUDE.md).

Add a new bundle by:

1. Vendoring the file under ``src/agent_memory_lite/ui/vendor/<file>``
   (use the pinned-version filename so future bumps are visible in
   git diff).
2. Listing it in ``_VENDOR_ASSETS`` below with the correct MIME.
3. Adding ``ui/vendor/<glob>`` to ``[tool.setuptools.package-data]`` in
   ``pyproject.toml`` if a new file extension or subdir is introduced.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(include_in_schema=False)

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_UI_ROOT = _PACKAGE_ROOT / "ui"
_VENDOR_ROOT = _UI_ROOT / "vendor"

_NO_CACHE = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}

_VENDOR_ASSETS: dict[str, str] = {
    # 2.2 (Phase 2.4): D3 vendored locally instead of CDN. Restores the
    # local-only philosophy and removes the SRI-mismatch failure mode
    # discovered in 2.1.5 (commit f3b3754 fixed an invalid hash).
    "d3.v7.8.5.min.js": "application/javascript; charset=utf-8",
}


@router.get("/ui/vendor/{asset_name}")
def memory_ui_vendor_asset(asset_name: str) -> FileResponse:
    """Serve a vendored JS/CSS asset from the package's ui/vendor dir.

    Unknown asset names fall back to the dashboard index so the browser
    receives a real HTML page instead of a 404 — same defensive default
    as the sibling ``/ui/{asset_name}`` handler in ui.py.
    """
    if asset_name not in _VENDOR_ASSETS:
        return FileResponse(
            _UI_ROOT / "index.html",
            media_type="text/html; charset=utf-8",
            headers=_NO_CACHE,
        )
    return FileResponse(
        _VENDOR_ROOT / asset_name,
        media_type=_VENDOR_ASSETS[asset_name],
    )
