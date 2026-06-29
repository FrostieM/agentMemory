"""Read-path workspace availability decision shared by the HTTP + MCP read guards.

Split out of ``workspace_paths`` to keep each module at or below the 150-SLOC
ceiling. FastAPI-free on purpose: the MCP read guard imports this directly so the
discipline's first MCP call (impact_check / brief / search) never pays the
``api.errors`` -> FastAPI cold-start cost that the write handlers defer.

Read/write asymmetry on a CORRUPT registry (deliberate -- see ``workspace_registry``
for the corrupt-vs-absent signal): the WRITE guard (``ensure_workspace_matches_db``)
fails CLOSED on a corrupt registry because a misrouted write CREATES persistent
cross-workspace pollution. The READ guard stays SOFT (this module refuses ONLY a
definite physical-file mismatch) because a misrouted read can only SURFACE
pre-existing pollution -- which the write guards already prevent -- and failing
closed here would (a) false-reject a read whose connection was correctly routed to
the workspace's own DB via X-Memory-DB-Path (registry-independent) and (b)
self-defeat ``GET /memory/status``: a default-workspace status call would 409 before
``build_environment`` could surface the very ``registry_load_error`` field the
operator needs to diagnose the corruption. So this guard never guesses when it
cannot resolve -- consistent with ``connection_matches_workspace``'s own design.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.config.workspace_paths import connection_matches_workspace


def read_workspace_unavailable_reason(
    conn: sqlite3.Connection, workspace_id: str, settings: Settings
) -> str | None:
    """Return a refusal reason when a READ on ``conn`` for ``workspace_id`` cannot be
    safely served, else ``None``. The single decision behind the HTTP
    ``ensure_workspace_readable_db`` guard and the MCP ``_read_guard`` so both stay
    in lockstep.

    Refuses ONLY on a DEFINITE physical-file mismatch (``connection_matches_workspace``
    returns ``False`` -- the read reached a different file than the workspace's
    registered DB). Stays SOFT (``None``) for every ambiguous case it returns
    ``True`` for: the anchor workspace, a genuinely-unregistered non-anchor
    workspace, an in-memory/temp connection, a resolution glitch, and a CORRUPT
    registry (where the expected path cannot be resolved -- see the module docstring
    for why reads stay soft there while writes fail closed).
    """
    if connection_matches_workspace(conn, workspace_id, settings):
        return None
    return (
        "the read reached a different database than this workspace's registered DB "
        "(definite cross-database mismatch)"
    )
