"""Post-build hooks for memory_get_context.

Pulled out of ``context.py`` to keep the route file under the 150 SLOC
ceiling. Each hook is opportunistic — gated by an env flag and skipped
silently when off — so the route stays focused on retrieval and
response assembly.

Both the HTTP route (``api/routes/context.py``) and the MCP stdio local
fallback (``mcp/stdio_handlers_episodes.py``) call
``apply_post_build_hooks`` so v1.5 / v1.6 / v2.2 / v2.3 fire the same
way regardless of which surface the agent reached. Without this,
MCP-only deployments (no HTTP service) silently miss the traffic-side
hooks.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_memory_lite.capability.behavior_apply import mark_behavior_instructions_applied
from agent_memory_lite.config.settings import Settings
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.maintenance.sentinel_scheduler import maybe_run_sentinels
from agent_memory_lite.retrieval.context_builder_models import BuiltContext
from agent_memory_lite.retrieval.last_retrieved_tracker import RetrievalUpdate, mark_retrieved
from agent_memory_lite.retrieval.pending_review import load_pending_review, render_pending_review
from agent_memory_lite.vector_store.base import VectorStore


def apply_post_build_hooks(
    conn: sqlite3.Connection,
    *,
    settings: Settings,
    request_workspace_id: str,
    built: BuiltContext,
    envelope_text: str,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
) -> str:
    """Run every post-build hook and return the (possibly-extended) envelope.

    Single chokepoint for HTTP + MCP paths. Each hook is independently
    gated; call order matches the historical sequence so behaviour stays
    byte-equivalent for HTTP callers.
    """
    maybe_track_behavior_application(
        conn, settings=settings, request_workspace_id=request_workspace_id, built=built
    )
    maybe_track_last_retrieved(
        conn, settings=settings, request_workspace_id=request_workspace_id, built=built
    )
    maybe_schedule_sentinels(
        conn,
        settings=settings,
        request_workspace_id=request_workspace_id,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    return inject_pending_review(
        conn, workspace_id=request_workspace_id, envelope_text=envelope_text
    )


def maybe_track_behavior_application(
    conn: sqlite3.Connection,
    *,
    settings: Settings,
    request_workspace_id: str,
    built: BuiltContext,
) -> None:
    """v1.5: bump behavior_instructions.application_count for own-workspace reads."""
    if not settings.behavior_apply_tracking_enabled:
        return
    if request_workspace_id != settings.workspace_id:
        return
    if built.behavior_instructions is None:
        return
    ids = [i.id for i in built.behavior_instructions.instructions]
    if not ids:
        return
    mark_behavior_instructions_applied(conn, workspace_id=request_workspace_id, instruction_ids=ids)
    conn.commit()


def maybe_track_last_retrieved(
    conn: sqlite3.Connection,
    *,
    settings: Settings,
    request_workspace_id: str,
    built: BuiltContext,
) -> None:
    """v1.6: stamp last_retrieved_at on top-K rows for the cold scanner."""
    if not settings.cold_tracking_enabled:
        return
    if request_workspace_id != settings.workspace_id:
        return
    updates = [
        RetrievalUpdate(kind="chunk", ids=tuple(hit.id for hit in built.hits)),
        RetrievalUpdate(kind="decision", ids=tuple(d.id for d in built.decisions)),
        RetrievalUpdate(kind="theory", ids=tuple(t.theory.id for t in built.theories)),
    ]
    mark_retrieved(
        conn,
        workspace_id=request_workspace_id,
        updates=updates,
        audit_batch_size=settings.cold_tracking_audit_batch_size,
    )
    conn.commit()


def maybe_schedule_sentinels(
    conn: sqlite3.Connection,
    *,
    settings: Settings,
    request_workspace_id: str,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> None:
    """v2.3 trigger-on-traffic background sentinel run.

    Project mode skips foreign workspaces; hub mode fires for any. The
    DB file is resolved from the request-scoped connection (PRAGMA
    database_list) so hub-mode reads write last_sentinel_run_at into
    the right workspace_meta — not the singleton settings.db_path.
    """
    if not settings.hub_mode and request_workspace_id != settings.workspace_id:
        return
    db_path = _resolve_db_path_from_conn(conn, settings)
    maybe_run_sentinels(
        workspace_id=request_workspace_id,
        settings=settings,
        db_path=db_path,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )


def _resolve_db_path_from_conn(conn: sqlite3.Connection, settings: Settings) -> Path:
    """Return the on-disk file the request-scoped connection points at."""
    try:
        for row in conn.execute("PRAGMA database_list"):
            if row[1] == "main" and row[2]:
                return Path(str(row[2]))
    except sqlite3.Error:
        pass
    return settings.db_path


def inject_pending_review(
    conn: sqlite3.Connection, *, workspace_id: str, envelope_text: str
) -> str:
    """v1.7: surface pending decision/insight candidates inside the envelope."""
    if "</memory_context>" not in envelope_text:
        return envelope_text
    review = load_pending_review(conn, workspace_id=workspace_id)
    review_lines = render_pending_review(review)
    if not review_lines:
        return envelope_text
    injection = "\n".join(review_lines) + "\n"
    return envelope_text.replace("</memory_context>", injection + "</memory_context>", 1)
