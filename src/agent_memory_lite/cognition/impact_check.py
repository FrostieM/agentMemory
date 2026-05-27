"""Impact_check primitive — one-shot pre-edit / pre-read analysis of one file.

This is a **discipline primitive**. Its job is to make "ask the graph
before reading the file" cheaper than "Read+Grep until you understand".

Where the old surface spread the same analysis across separate digest,
graph, and file-read steps, this primitive returns everything an agent
needs to make the
"can I just edit this safely?" decision in **one** envelope:

  * **Digest** — purpose_short, language, top symbols, staleness flag.
  * **Callers** — every inbound edge from the rest of the codebase
    into symbols defined in this file (who would break if signatures
    change).
  * **Hot symbols** — symbols with ≥ N callers, ranked by callers count.
    These are the high-risk surface the agent should treat with care.
  * **Verdict** — single-word risk rollup:  ``low`` | ``medium`` |
    ``high`` | ``not_indexed``.
  * **Advisory** — one-sentence next-step hint pointing at the right
    follow-up tool when the verdict warrants it.

Failure-soft contract:  any SQL or schema mismatch returns a
``not_indexed`` verdict with an advisory that points the operator at
the digest pipeline.  The primitive never raises.

This module knows the canonical SQL.  It does NOT depend on FastAPI, MCP, or
any agent runtime.  HTTP route and MCP handler are thin wrappers.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class CallerEdge:
    """One inbound edge — caller's qualified name + source location."""

    qualified_name: str
    src_chunk_id: str
    src_file_path: str | None
    edge_type: str


@dataclass(frozen=True, slots=True)
class HotSymbol:
    """A symbol in this file with at least ``hot_threshold`` inbound callers."""

    qualified_name: str
    callers_count: int


@dataclass(frozen=True, slots=True)
class ImpactReport:
    """Full envelope returned by ``impact_check``."""

    file_path: str
    digest: dict[str, Any] = field(default_factory=dict)
    callers: list[CallerEdge] = field(default_factory=list)
    hot_symbols: list[HotSymbol] = field(default_factory=list)
    verdict: str = "not_indexed"
    advisory: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "digest": self.digest,
            "callers": [
                {
                    "qualified_name": c.qualified_name,
                    "src_chunk_id": c.src_chunk_id,
                    "src_file_path": c.src_file_path,
                    "edge_type": c.edge_type,
                }
                for c in self.callers
            ],
            "hot_symbols": [
                {"qualified_name": h.qualified_name, "callers_count": h.callers_count}
                for h in self.hot_symbols
            ],
            "verdict": self.verdict,
            "advisory": self.advisory,
        }


# ============================================================
# Verdict + advisory copy
# ============================================================


_VERDICT_THRESHOLDS = {
    # callers >= 6 OR any hot symbol → high
    "high": 6,
    # 1..5 callers and no hot symbols → medium
    "medium": 1,
}

_ADVISORY_BY_VERDICT = {
    "not_indexed": (
        "File not in code_digests yet. Run PostToolUse digest hook or call "
        "memory_get(kind='code_digest', id='<file_path>') after the next edit. "
        "Falling back to Read is acceptable but blind to downstream impact."
    ),
    "low": ("Low impact: no recorded callers. Safe to edit with normal review."),
    "medium": (
        "Medium impact: 1-5 callers. Use memory_get(kind='code_digest', "
        "id='<file>', fields=['top_callers_json']) for the full caller list "
        "before changing signatures."
    ),
    "high": (
        "HIGH impact: hub file (≥6 callers or hot symbols). Fetch the "
        "code_digest full caller fields with memory_get(kind='code_digest', "
        "id='<file>') and use a focused source read before changing signatures."
    ),
}


def _compute_verdict(*, indexed: bool, callers_count: int, hot_count: int) -> str:
    if not indexed:
        return "not_indexed"
    if callers_count >= _VERDICT_THRESHOLDS["high"] or hot_count > 0:
        return "high"
    if callers_count >= _VERDICT_THRESHOLDS["medium"]:
        return "medium"
    return "low"


# ============================================================
# SQL helpers
# ============================================================


def _load_digest(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    file_path: str,
    project_root: Path | None = None,
) -> dict[str, Any] | None:
    """Pull the code_digests row for ``file_path`` into a compact dict."""
    row = None
    for candidate in _path_candidates(file_path, project_root=project_root):
        if project_root is not None and not _stored_path_exists(project_root, candidate):
            continue
        row = conn.execute(
            """
            SELECT file_path, file_sha1, language, chunk_count, symbol_count,
                   inbound_edge_count, outbound_edge_count, pagerank,
                   purpose_short, top_symbols_json, top_callers_json,
                   last_indexed_at, updated_at
            FROM code_digests
            WHERE workspace_id = ? AND file_path = ?
            """,
            (workspace_id, candidate),
        ).fetchone()
        if row is not None:
            break
    if row is None:
        return None
    top_symbols: list[Any] = []
    try:
        raw_syms = row[9]
        if raw_syms:
            top_symbols = json.loads(raw_syms)
    except (TypeError, ValueError):
        top_symbols = []
    digest: dict[str, Any] = {
        "file_path": row[0],
        "file_sha1": row[1],
        "language": row[2],
        "chunk_count": row[3],
        "symbol_count": row[4],
        "inbound_edge_count": row[5] or 0,
        "outbound_edge_count": row[6] or 0,
        "pagerank": row[7],
        "purpose_short": row[8] or "",
        "top_symbols": top_symbols,
        "last_indexed_at": row[11] or "",
    }
    # Staleness flag: if file_sha1 disagrees with the on-disk SHA the agent
    # cannot compute (we don't read FS here), but `is_stale` says "trust
    # this digest only if last_indexed_at is recent".  Threshold 2h matches
    # the acceptance gate's `newest_age_minutes` target.
    digest["is_stale"] = _is_stale(row[11])
    return digest


def _infer_project_root(conn: sqlite3.Connection) -> Path | None:
    """Infer project root from a standard ``.agent_memory/memory.db`` path."""
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return None
    for row in rows:
        name = str(row["name"] if isinstance(row, sqlite3.Row) else row[1])
        raw_path = str(row["file"] if isinstance(row, sqlite3.Row) else row[2])
        if name != "main" or not raw_path or raw_path == ":memory:":
            continue
        db_path = Path(raw_path).resolve()
        if db_path.name == "memory.db" and db_path.parent.name == ".agent_memory":
            return db_path.parent.parent
        return None
    return None


def _norm_path(path: str) -> str:
    return path.strip().replace("\\", "/").removeprefix("./")


def _relative_to(path: Path, root: Path) -> str | None:
    try:
        return _norm_path(str(path.resolve(strict=False).relative_to(root.resolve())))
    except ValueError:
        return None


def _path_candidates(file_path: str, *, project_root: Path | None) -> list[str]:
    """Return preferred DB path keys for a user-provided path."""
    raw = file_path.strip()
    normalized = _norm_path(raw)
    candidates: list[str] = []

    def add(value: str | None) -> None:
        if value and value not in candidates:
            candidates.append(value)

    path_obj = Path(raw)
    if path_obj.is_absolute():
        if project_root is not None:
            add(_relative_to(path_obj, project_root))
        add(_norm_path(str(path_obj)))
        return candidates

    if project_root is not None and not normalized.startswith("src/"):
        package_path = project_root / "src" / "agent_memory_lite" / normalized
        if package_path.exists():
            add(_norm_path(f"src/agent_memory_lite/{normalized}"))
    add(normalized)
    prefix = "src/agent_memory_lite/"
    if normalized.startswith(prefix):
        add(normalized.removeprefix(prefix))
    elif normalized.startswith("agent_memory_lite/"):
        add(f"src/{normalized}")
    return candidates


def _stored_path_exists(project_root: Path, stored_path: str) -> bool:
    path = Path(stored_path)
    if path.is_absolute():
        return path.exists()
    normalized = _norm_path(stored_path)
    if (project_root / normalized).exists():
        return True
    if not normalized.startswith("src/"):
        return (project_root / "src" / "agent_memory_lite" / normalized).exists()
    return False


def _is_stale(last_indexed_at: str | None, threshold_minutes: int = 120) -> bool:
    """True when the digest was indexed more than ``threshold_minutes`` ago."""
    if not last_indexed_at:
        return True
    import calendar  # noqa: PLC0415 — local convenience for UTC parsing

    try:
        ts = calendar.timegm(time.strptime(last_indexed_at, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return True
    age_min = (time.time() - ts) / 60.0
    return age_min > threshold_minutes


def _load_callers(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    file_path: str,
    limit: int,
) -> list[CallerEdge]:
    """Find every inbound edge whose dst chunk lives in ``file_path``."""
    # symbol_edges.dst_chunk_id → chunks.id → chunks.file_id → files.path
    # Inbound = edges where dst is in this file, src is not (or could be).
    rows = conn.execute(
        """
        SELECT
          e.src_qualified_name,
          e.src_chunk_id,
          src_file.path AS src_file_path,
          e.edge_type
        FROM symbol_edges e
        JOIN chunks dst_c ON dst_c.id = e.dst_chunk_id AND dst_c.workspace_id = e.workspace_id
        JOIN files dst_file ON dst_file.id = dst_c.file_id
        LEFT JOIN chunks src_c ON src_c.id = e.src_chunk_id
        LEFT JOIN files src_file ON src_file.id = src_c.file_id
        WHERE e.workspace_id = ?
          AND dst_file.path = ?
          AND (src_file.path IS NULL OR src_file.path != ?)
        ORDER BY e.created_at DESC
        LIMIT ?
        """,
        (workspace_id, file_path, file_path, limit),
    ).fetchall()
    return [
        CallerEdge(
            qualified_name=str(row[0] or "<unknown>"),
            src_chunk_id=str(row[1] or ""),
            src_file_path=row[2],
            edge_type=str(row[3] or "calls"),
        )
        for row in rows
    ]


def _load_hot_symbols(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    file_path: str,
    threshold: int,
) -> list[HotSymbol]:
    """Find symbols in this file with at least ``threshold`` inbound callers."""
    rows = conn.execute(
        """
        SELECT
          e.dst_qualified_name,
          COUNT(DISTINCT e.src_qualified_name) AS callers_count
        FROM symbol_edges e
        JOIN chunks dst_c ON dst_c.id = e.dst_chunk_id AND dst_c.workspace_id = e.workspace_id
        JOIN files dst_file ON dst_file.id = dst_c.file_id
        WHERE e.workspace_id = ?
          AND dst_file.path = ?
          AND e.dst_qualified_name IS NOT NULL
          AND e.dst_qualified_name != ''
        GROUP BY e.dst_qualified_name
        HAVING COUNT(DISTINCT e.src_qualified_name) >= ?
        ORDER BY callers_count DESC
        LIMIT 10
        """,
        (workspace_id, file_path, threshold),
    ).fetchall()
    return [HotSymbol(qualified_name=str(row[0]), callers_count=int(row[1])) for row in rows]


# ============================================================
# Public entrypoint
# ============================================================


def impact_check(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    file_path: str,
    callers_limit: int = 20,
    hot_threshold: int = 3,
    project_root: Path | str | None = None,
) -> ImpactReport:
    """One-shot pre-edit / pre-read impact analysis for ``file_path``.

    Returns an ``ImpactReport``.  Never raises:  schema errors / missing
    file → ``verdict='not_indexed'`` with an advisory.

    Token target:  ~80-150 tokens for the full envelope, so calling this
    is cheaper than ``Read``-ing even a small file.
    """
    root = Path(project_root).resolve() if project_root is not None else _infer_project_root(conn)
    try:
        digest = _load_digest(
            conn,
            workspace_id=workspace_id,
            file_path=file_path,
            project_root=root,
        )
    except sqlite3.Error:
        return ImpactReport(
            file_path=file_path,
            verdict="not_indexed",
            advisory=_ADVISORY_BY_VERDICT["not_indexed"],
        )
    if digest is None:
        return ImpactReport(
            file_path=file_path,
            verdict="not_indexed",
            advisory=_ADVISORY_BY_VERDICT["not_indexed"],
        )
    resolved_file_path = str(digest.get("file_path") or file_path)
    try:
        callers = _load_callers(
            conn, workspace_id=workspace_id, file_path=resolved_file_path, limit=callers_limit
        )
        hot_symbols = _load_hot_symbols(
            conn,
            workspace_id=workspace_id,
            file_path=resolved_file_path,
            threshold=hot_threshold,
        )
    except sqlite3.Error:
        callers = []
        hot_symbols = []
    callers_count = int(digest.get("inbound_edge_count") or 0)
    # If we have a non-zero digest count but the join returned zero, prefer
    # the digest figure — the join can be empty when the file lives outside
    # the `files` table (e.g. ingested without the file row).
    if not callers and callers_count == 0:
        callers_count = len(callers)
    verdict = _compute_verdict(
        indexed=True,
        callers_count=callers_count,
        hot_count=len(hot_symbols),
    )
    advisory = _ADVISORY_BY_VERDICT.get(verdict, "")
    if digest.get("is_stale"):
        advisory = (
            "[stale digest — last_indexed_at older than 2h; rerun PostToolUse hook] " + advisory
        )
    return ImpactReport(
        file_path=resolved_file_path,
        digest=digest,
        callers=callers,
        hot_symbols=hot_symbols,
        verdict=verdict,
        advisory=advisory,
    )
