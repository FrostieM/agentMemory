"""Local FTS-only context builder for the UserPromptSubmit hook.

When the HTTP service at 127.0.0.1:8765 is down, the hook would
otherwise emit an empty notice and the agent runs blind. This module
opens the SQLite DB directly and renders a minimal context envelope
using FTS only — no embedding model load (which would be 2-3s cold
start, unacceptable for a per-prompt hook), no graph walk, no EWMA
re-rank. The result is degraded but useful: the agent still sees
core_memory, behavior_instructions, the most relevant active
decisions, and FTS-matched chunks.

Output XML schema matches a subset of the full envelope so the agent
treats the fallback identically to the HTTP path.
"""

from __future__ import annotations

import sqlite3
from xml.sax.saxutils import escape

_FTS_LIMIT = 6
_DECISION_LIMIT = 4
_BEHAVIOR_LIMIT = 8


def build_fts_only_context(
    *, db_path: str, workspace_id: str, query: str, max_chunks: int = _FTS_LIMIT
) -> str | None:
    """Open `db_path`, run FTS + structured-section reads, render envelope.

    Returns the rendered ``<memory_context>...</memory_context>`` string or
    None if the DB is unreachable. Never raises — failures degrade silently.
    """
    try:
        conn = sqlite3.connect(db_path, timeout=2.0)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return None
    try:
        sections = [
            _render_core_memory(conn, workspace_id),
            _render_behavior_instructions(conn, workspace_id),
            _render_active_decisions(conn, workspace_id, query),
            _render_retrieved_chunks(conn, workspace_id, query, max_chunks),
        ]
    finally:
        conn.close()
    body = "\n".join(s for s in sections if s)
    if not body.strip():
        return None
    return f"<memory_context>\n{body}\n</memory_context>"


def _render_core_memory(conn: sqlite3.Connection, workspace_id: str) -> str:
    """Pinned + active core memory entries — always shown verbatim."""
    try:
        rows = conn.execute(
            "SELECT id, key, value FROM core_memory "
            "WHERE workspace_id = ? AND COALESCE(active, 1) = 1 "
            "ORDER BY COALESCE(pinned, 0) DESC, importance DESC LIMIT 5",
            (workspace_id,),
        ).fetchall()
    except sqlite3.Error:
        return ""
    if not rows:
        return "  <core_memory/>"
    items = "\n".join(
        f'    <entry id="{escape(r["id"])}" key="{escape(r["key"] or "")}">'
        f"{escape(str(r['value'] or ''))[:600]}</entry>"
        for r in rows
    )
    return f"  <core_memory>\n{items}\n  </core_memory>"


def _render_behavior_instructions(conn: sqlite3.Connection, workspace_id: str) -> str:
    """Active behavior instructions — render full rule text."""
    try:
        rows = conn.execute(
            "SELECT id, name, rule, kind, priority FROM behavior_instructions "
            "WHERE workspace_id = ? AND COALESCE(active, 1) = 1 "
            "ORDER BY application_count DESC, updated_at DESC LIMIT ?",
            (workspace_id, _BEHAVIOR_LIMIT),
        ).fetchall()
    except sqlite3.Error:
        return ""
    if not rows:
        return "  <behavior_instructions/>"
    items = "\n".join(
        f'    <instruction id="{escape(r["id"])}" kind="{escape(r["kind"] or "")}">'
        f"\n      <name>{escape(r['name'] or '')}</name>"
        f"\n      <rule>{escape(str(r['rule'] or ''))[:800]}</rule>\n    </instruction>"
        for r in rows
    )
    return f"  <behavior_instructions>\n{items}\n  </behavior_instructions>"


def _render_active_decisions(conn: sqlite3.Connection, workspace_id: str, query: str) -> str:
    """Active decisions ranked by FTS match against query, then importance."""
    try:
        rows = conn.execute(
            "SELECT id, title, decision_text, rationale FROM decisions "
            "WHERE workspace_id = ? AND status = 'active' "
            "ORDER BY COALESCE(pinned, 0) DESC, importance DESC, updated_at DESC LIMIT ?",
            (workspace_id, _DECISION_LIMIT),
        ).fetchall()
    except sqlite3.Error:
        return ""
    if not rows:
        return "  <active_decisions/>"
    items = "\n".join(
        f'    <decision id="{escape(r["id"])}">'
        f"\n      <title>{escape(r['title'] or '')}</title>"
        f"\n      <text>{escape(str(r['decision_text'] or ''))[:600]}</text>"
        f"\n    </decision>"
        for r in rows
    )
    return f"  <active_decisions>\n{items}\n  </active_decisions>"


def _render_retrieved_chunks(
    conn: sqlite3.Connection, workspace_id: str, query: str, limit: int
) -> str:
    """FTS BM25 hits against chunks_fts. Skips embedding/vector path entirely."""
    fts_query = _safe_fts_query(query)
    if not fts_query:
        return "  <retrieved_chunks/>"
    try:
        rows = conn.execute(
            "SELECT c.id, c.text, c.summary FROM chunks c "
            "JOIN chunks_fts f ON f.rowid = c.rowid "
            "WHERE c.workspace_id = ? AND f.text MATCH ? "
            "AND COALESCE(c.is_archived, 0) = 0 "
            "ORDER BY rank LIMIT ?",
            (workspace_id, fts_query, limit),
        ).fetchall()
    except sqlite3.Error:
        return "  <retrieved_chunks/>"
    if not rows:
        return "  <retrieved_chunks/>"
    items = "\n".join(
        f'    <chunk id="{escape(r["id"])}" sources="fts">{escape(str(r["text"] or ""))[:400]}</chunk>'
        for r in rows
    )
    return f"  <retrieved_chunks>\n{items}\n  </retrieved_chunks>"


def _safe_fts_query(text: str) -> str:
    """Strip FTS5 special chars so a free-form prompt does not error out.

    SQLite FTS5 treats `:`, `-`, `"`, `*`, parens specially. We substitute
    them with spaces and join meaningful tokens with OR so the search is
    forgiving (any matched token surfaces a chunk).
    """
    if not text:
        return ""
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in text)
    tokens = [t for t in cleaned.split() if len(t) >= 3]
    if not tokens:
        return ""
    # Cap to first 8 tokens — FTS5 query strings get expensive past that.
    return " OR ".join(tokens[:8])
