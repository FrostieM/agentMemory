"""1.6.0: symbol-version recording for the file ingest pipeline.

After every chunk is persisted, this helper records a
``symbol_versions`` row when the chunk's content has changed
relative to the most recent version for the same
(workspace_id, qualified_name).

Idempotent by content_hash: re-ingesting unchanged code never
produces a new version row, so the history stays clean.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.extraction.signature_extractor import (
    content_hash,
    extract_signature,
    signature_hash,
)
from agent_memory_lite.models.chunks import Chunk
from agent_memory_lite.models.symbol_versions import SymbolVersionIn
from agent_memory_lite.repositories.symbol_versions_repo import (
    get_latest_version,
    insert_version,
)


def record_versions_for_chunks(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    chunks: list[Chunk],
    file_path: str,
    language: str | None,
) -> list[str]:
    """For every chunk with a qualified_name, append a symbol_versions
    row when the content_hash differs from the most recent version.
    Returns the qualified_names that got a NEW version this pass —
    the soft-edge accumulator uses this list to build co_changed
    pairs (1.7.0).
    """
    changed: list[str] = []
    for chunk in chunks:
        if not chunk.qualified_name:
            continue
        chash = content_hash(chunk.text)
        latest = get_latest_version(
            conn,
            workspace_id=workspace_id,
            qualified_name=chunk.qualified_name,
        )
        if latest is not None and latest.content_hash == chash:
            continue
        signature = extract_signature(chunk.text)
        insert_version(
            conn,
            SymbolVersionIn(
                workspace_id=workspace_id,
                qualified_name=chunk.qualified_name,
                file_path=file_path,
                chunk_id=chunk.id,
                language=language,
                signature_text=signature,
                signature_hash=signature_hash(signature),
                content_hash=chash,
            ),
        )
        changed.append(chunk.qualified_name)
    return changed
