"""1.6.0: breaking-change detection query.

Split from ``symbol_versions_repo.py`` so the basic CRUD module stays
under the SLOC ceiling. The query joins each ``symbol_versions`` row
to its immediately-prior version for the same (workspace_id,
qualified_name) and filters for signature_hash mismatches —
i.e. structural changes that may break downstream callers.
"""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.symbol_versions import SymbolVersion

_BREAKING_SQL = """
SELECT current.id AS cur_id,
       current.workspace_id AS cur_ws,
       current.qualified_name AS cur_qn,
       current.file_path AS cur_file,
       current.chunk_id AS cur_chunk,
       current.language AS cur_lang,
       current.signature_text AS cur_sig,
       current.signature_hash AS cur_sighash,
       current.content_hash AS cur_chash,
       current.created_at AS cur_at,
       current.metadata_json AS cur_meta,
       prev.id AS prev_id,
       prev.workspace_id AS prev_ws,
       prev.qualified_name AS prev_qn,
       prev.file_path AS prev_file,
       prev.chunk_id AS prev_chunk,
       prev.language AS prev_lang,
       prev.signature_text AS prev_sig,
       prev.signature_hash AS prev_sighash,
       prev.content_hash AS prev_chash,
       prev.created_at AS prev_at,
       prev.metadata_json AS prev_meta
FROM symbol_versions AS current
JOIN symbol_versions AS prev
  ON prev.workspace_id = current.workspace_id
 AND prev.qualified_name = current.qualified_name
 AND prev.created_at < current.created_at
WHERE current.workspace_id = ?
  AND current.created_at >= ?
  AND current.signature_hash <> prev.signature_hash
  AND prev.created_at = (
      SELECT MAX(p2.created_at) FROM symbol_versions p2
      WHERE p2.workspace_id = current.workspace_id
        AND p2.qualified_name = current.qualified_name
        AND p2.created_at < current.created_at
  )
ORDER BY current.created_at DESC
LIMIT ?
"""


def _row_to_pair(r: sqlite3.Row) -> tuple[SymbolVersion, SymbolVersion]:
    cur = SymbolVersion(
        id=r["cur_id"],
        workspace_id=r["cur_ws"],
        qualified_name=r["cur_qn"],
        file_path=r["cur_file"],
        chunk_id=r["cur_chunk"],
        language=r["cur_lang"],
        signature_text=r["cur_sig"],
        signature_hash=r["cur_sighash"],
        content_hash=r["cur_chash"],
        created_at=r["cur_at"],
        metadata=json.loads(r["cur_meta"] or "{}"),
    )
    prev = SymbolVersion(
        id=r["prev_id"],
        workspace_id=r["prev_ws"],
        qualified_name=r["prev_qn"],
        file_path=r["prev_file"],
        chunk_id=r["prev_chunk"],
        language=r["prev_lang"],
        signature_text=r["prev_sig"],
        signature_hash=r["prev_sighash"],
        content_hash=r["prev_chash"],
        created_at=r["prev_at"],
        metadata=json.loads(r["prev_meta"] or "{}"),
    )
    return prev, cur


def list_recent_signature_changes(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    since_iso: str,
    limit: int = 100,
) -> list[tuple[SymbolVersion, SymbolVersion]]:
    """Return (prev, current) pairs whose signature_hash differs and
    where current.created_at >= since_iso. Each pair is at most one
    per (workspace_id, qualified_name) — the most recent breaking
    change wins.
    """
    rows = conn.execute(_BREAKING_SQL, (workspace_id, since_iso, limit)).fetchall()
    return [_row_to_pair(r) for r in rows]
