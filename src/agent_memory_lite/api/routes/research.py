"""Research-lab memory write routes.

Snapshot / experiment / result routes live in ``research_snapshots.py``;
concept / insight routes live in ``research_taxonomy.py``. Both child
routers are mounted on the main router below.
"""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.routes.research_snapshots import router as snapshots_router
from agent_memory_lite.api.routes.research_taxonomy import router as taxonomy_router

router = APIRouter()
router.include_router(snapshots_router)
router.include_router(taxonomy_router)
