"""1.8.0: heuristic file-digest assembler.

Combines the file's chunks (qualified_names, symbol_kinds), its
incoming/outgoing edges, and its recent version count into a
compact narrative + structured fields. Pure derivation — no LLM,
no I/O beyond the SQLite reads we already do during ingest. The
v1.8.x roadmap includes an optional LLM enrichment pass; the
heuristic baseline ships first because it works without Ollama.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.ingestion.file_digest_llm import (
    maybe_llm_narrative,
    top_targets,
)
from agent_memory_lite.models.chunks import Chunk
from agent_memory_lite.models.file_digests import FileDigestIn

_RECENT_DAYS = 7


def _count_recent_versions(
    conn: sqlite3.Connection, *, workspace_id: str, file_path: str, since_iso: str
) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM symbol_versions "
        "WHERE workspace_id = ? AND file_path = ? AND created_at >= ?",
        (workspace_id, file_path, since_iso),
    ).fetchone()
    return int(row["n"]) if row is not None else 0


def _count_edges(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    qnames: list[str],
) -> tuple[int, int]:
    """Inbound + outbound edge totals for the file's symbols."""
    if not qnames:
        return 0, 0
    placeholders = ", ".join("?" * len(qnames))
    inbound_row = conn.execute(
        f"SELECT COUNT(*) AS n FROM symbol_edges "
        f"WHERE workspace_id = ? AND dst_qualified_name IN ({placeholders})",
        [workspace_id, *qnames],
    ).fetchone()
    outbound_row = conn.execute(
        f"SELECT COUNT(*) AS n FROM symbol_edges "
        f"WHERE workspace_id = ? AND src_qualified_name IN ({placeholders})",
        [workspace_id, *qnames],
    ).fetchone()
    inbound = int(inbound_row["n"]) if inbound_row is not None else 0
    outbound = int(outbound_row["n"]) if outbound_row is not None else 0
    return inbound, outbound


def _summarize_kinds(chunks: list[Chunk]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in chunks:
        if c.symbol_kind:
            out[c.symbol_kind] = out.get(c.symbol_kind, 0) + 1
    return out


def _build_narrative(
    *,
    file_path: str,
    language: str | None,
    chunks: list[Chunk],
    kinds: dict[str, int],
    inbound: int,
    outbound: int,
    versions_recent: int,
) -> str:
    parts: list[str] = []
    parts.append(f"{file_path}")
    if language:
        parts[-1] += f" ({language})"
    if kinds:
        kind_parts = [f"{n} {k}" + ("" if n == 1 else "s") for k, n in sorted(kinds.items())]
        parts.append("Contains: " + ", ".join(kind_parts) + ".")
    elif chunks:
        parts.append(f"{len(chunks)} chunk(s); no structural symbols recovered.")
    if inbound or outbound:
        parts.append(f"Edges: {inbound} inbound, {outbound} outbound.")
    if versions_recent:
        parts.append(
            f"Recent activity: {versions_recent} version(s) in the last {_RECENT_DAYS} days."
        )
    return " ".join(parts)


def build_digest(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    file_path: str,
    language: str | None,
    chunks: list[Chunk],
    last_indexed_at: str,
    settings: Settings | None = None,
) -> FileDigestIn:
    qnames = [c.qualified_name for c in chunks if c.qualified_name]
    kinds = _summarize_kinds(chunks)
    since_iso = (datetime.now(UTC) - timedelta(days=_RECENT_DAYS)).isoformat()
    versions_recent = _count_recent_versions(
        conn, workspace_id=workspace_id, file_path=file_path, since_iso=since_iso
    )
    inbound, outbound = _count_edges(conn, workspace_id=workspace_id, qnames=qnames)
    heuristic = _build_narrative(
        file_path=file_path,
        language=language,
        chunks=chunks,
        kinds=kinds,
        inbound=inbound,
        outbound=outbound,
        versions_recent=versions_recent,
    )
    narrative = heuristic
    narrative_source = "heuristic"
    inbound_targets, outbound_targets = top_targets(conn, workspace_id=workspace_id, qnames=qnames)
    llm_text = maybe_llm_narrative(
        settings=settings,
        file_path=file_path,
        language=language,
        qnames=qnames,
        inbound_targets=inbound_targets,
        outbound_targets=outbound_targets,
    )
    if llm_text:
        narrative = llm_text
        narrative_source = "llm"
    structured: dict[str, object] = {
        "qualified_names": qnames[:100],
        "kinds": kinds,
        "narrative_source": narrative_source,
    }
    return FileDigestIn(
        workspace_id=workspace_id,
        file_path=file_path,
        language=language,
        chunk_count=len(chunks),
        symbol_count=len(qnames),
        inbound_edge_count=inbound,
        outbound_edge_count=outbound,
        versions_recent=versions_recent,
        narrative=narrative,
        structured=structured,
        last_indexed_at=last_indexed_at,
    )
