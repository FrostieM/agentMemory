"""File digest worker — consume PostToolUse queue → UPSERT into code_digests.

The worker is the engine behind the "Code hubs" brief section. Each
file the agent edits goes through:

  1. PostToolUse hook drops a marker line into the queue file
     (one JSON line per edit: ``{"workspace_id", "file_path", "ts"}``).
  2. ``run_worker()`` polls the queue file, dedupes by latest mtime
     per (workspace, file_path), and consumes each entry.
  3. For each entry: read the file, compute sha1, run a heuristic
     digest pass (purpose_short = first docstring line; top_symbols
     = AST symbol scan), UPSERT into ``code_digests``.
  4. The next agent call to ``memory_get(kind="code_digest", id=...)``
     returns the fresh digest projection without reading source.

Design constraints (per the plan):

* Failure-soft: any one file's parse error is logged and skipped, never
  aborts the worker loop.
* Cheap pass on the hot path: heuristic symbol scan, no Ollama, no
  embedding. Ollama narrative is an optional second pass.
* Bounded queue: hard cap of 5,000 pending tasks; oldest tasks dropped
  on overflow so the worker stays steady-state.
* Pure Python: no extension deps. tree-sitter integration plugs in
  later via ``extraction/symbol_edges_*`` (already exists in v2 surface).

This module is the in-process worker logic. The OS-level daemon
wrapper lives in ``scripts/memory_consolidation_task.ps1`` (Windows)
or ``scripts/memory_consolidation_task.sh`` (POSIX, future).
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent_memory_lite.digest_worker")

# ============================================================
# Queue file format + helpers
# ============================================================

DEFAULT_QUEUE_PATH = Path.home() / ".agent_memory" / "digest_queue.jsonl"
MAX_PENDING = 5_000


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """One enqueued file edit awaiting digest refresh."""

    workspace_id: str
    db_path: str
    file_path: str
    ts: float


def enqueue(
    *,
    workspace_id: str,
    db_path: str,
    file_path: str,
    queue_path: Path | None = None,
) -> None:
    """Append one entry to the queue file. Used by the PostToolUse hook."""
    path = queue_path or DEFAULT_QUEUE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "workspace_id": workspace_id,
        "db_path": db_path,
        "file_path": file_path,
        "ts": time.time(),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_queue(queue_path: Path) -> list[QueueEntry]:
    """Load every entry, drop malformed lines, hard-cap to MAX_PENDING newest."""
    if not queue_path.exists():
        return []
    out: list[QueueEntry] = []
    try:
        with queue_path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                ws = str(obj.get("workspace_id", ""))
                db = str(obj.get("db_path", ""))
                fp = str(obj.get("file_path", ""))
                ts = float(obj.get("ts", 0.0))
                if not ws or not db or not fp:
                    continue
                out.append(QueueEntry(workspace_id=ws, db_path=db, file_path=fp, ts=ts))
    except OSError as exc:
        logger.warning("digest_queue_read_failed", extra={"error": str(exc)})
        return []
    if len(out) > MAX_PENDING:
        out = out[-MAX_PENDING:]
    return out


def _dedupe_entries(entries: list[QueueEntry]) -> list[QueueEntry]:
    """Keep only the newest entry per (workspace_id, file_path)."""
    latest: dict[tuple[str, str], QueueEntry] = {}
    for entry in entries:
        key = (entry.workspace_id, entry.file_path)
        prior = latest.get(key)
        if prior is None or entry.ts > prior.ts:
            latest[key] = entry
    return sorted(latest.values(), key=lambda e: e.ts)


def _clear_queue(queue_path: Path) -> None:
    """Truncate the queue file. Called after a successful drain."""
    try:
        queue_path.write_text("", encoding="utf-8")
    except OSError as exc:
        logger.warning("digest_queue_clear_failed", extra={"error": str(exc)})


# ============================================================
# Per-file digest (heuristic — fast, deterministic)
# ============================================================


def _detect_language(file_path: str) -> str | None:
    suffix = Path(file_path).suffix.lower()
    mapping = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".js": "javascript",
        ".jsx": "jsx",
        ".sql": "sql",
        ".md": "markdown",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".json": "json",
        ".toml": "toml",
        ".ps1": "powershell",
        ".sh": "shell",
    }
    return mapping.get(suffix)


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()


def _python_purpose_short(source: str) -> str:
    """Return the first sentence of the module docstring, ≤80 chars."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    doc = ast.get_docstring(tree)
    if not doc:
        return ""
    first_line = doc.strip().splitlines()[0].strip()
    return first_line[:80]


def _python_top_symbols(source: str, limit: int = 5) -> list[dict[str, Any]]:
    """Return the first N top-level def/class names with their start line."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    symbols: list[dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            symbols.append({"name": node.name, "kind": "function", "line": node.lineno})
        elif isinstance(node, ast.ClassDef):
            symbols.append({"name": node.name, "kind": "class", "line": node.lineno})
        if len(symbols) >= limit:
            break
    return symbols


def _generic_top_symbols(source: str, limit: int = 5) -> list[dict[str, Any]]:
    """Generic heuristic: first ``limit`` non-blank lines that look like declarations."""
    declaration_prefixes = ("function ", "class ", "def ", "## ", "### ")
    comment_prefixes = ("//", "/*")  # markdown ## handled inline; bare # skipped below
    symbols: list[dict[str, Any]] = []
    for line_no, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        # Check declaration prefixes BEFORE the comment skip so Markdown
        # `## heading` lines (which technically start with `#`) survive.
        if any(stripped.startswith(prefix) for prefix in declaration_prefixes):
            symbols.append({"name": stripped[:60], "kind": "header", "line": line_no})
            if len(symbols) >= limit:
                break
            continue
        # Skip clear non-declaration comments.
        if stripped.startswith(comment_prefixes) or (
            stripped.startswith("#") and not stripped.startswith("##")
        ):
            continue
    return symbols


def _first_line_purpose(source: str) -> str:
    """For non-Python files, take the first non-blank, non-shebang line, ≤80 chars."""
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#!"):
            continue
        return stripped[:80]
    return ""


@dataclass(frozen=True, slots=True)
class DigestResult:
    """In-memory representation of a digest pass. Persists to code_digests row."""

    file_path: str
    file_sha1: str
    language: str | None
    purpose_short: str
    top_symbols: list[dict[str, Any]]
    symbol_count: int
    chunk_count: int


def compute_digest(file_path: str, content: str) -> DigestResult:
    """Run the heuristic digest pass on one file's text.

    Pure function — no DB, no I/O beyond the input string. Returns a
    DigestResult ready for UPSERT into code_digests.
    """
    language = _detect_language(file_path)
    if language == "python":
        purpose = _python_purpose_short(content)
        symbols = _python_top_symbols(content)
    else:
        purpose = _first_line_purpose(content)
        symbols = _generic_top_symbols(content)
    return DigestResult(
        file_path=file_path,
        file_sha1=_sha1(content),
        language=language,
        purpose_short=purpose,
        top_symbols=symbols,
        symbol_count=len(symbols),
        chunk_count=max(1, content.count("\n") // 50),  # rough chunk estimate
    )


# ============================================================
# SQL upsert
# ============================================================


def upsert_digest(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    result: DigestResult,
) -> str:
    """Insert or replace one code_digests row. Returns the digest id."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    existing = conn.execute(
        "SELECT id FROM code_digests WHERE workspace_id = ? AND file_path = ?",
        (workspace_id, result.file_path),
    ).fetchone()
    digest_id = existing[0] if existing else f"digest_{uuid.uuid4().hex[:16]}"
    conn.execute(
        """
        INSERT INTO code_digests (
            id, workspace_id, file_path, file_sha1, language,
            chunk_count, symbol_count, purpose_short, top_symbols_json,
            last_indexed_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, file_path) DO UPDATE SET
            file_sha1=excluded.file_sha1,
            language=excluded.language,
            chunk_count=excluded.chunk_count,
            symbol_count=excluded.symbol_count,
            purpose_short=excluded.purpose_short,
            top_symbols_json=excluded.top_symbols_json,
            last_indexed_at=excluded.last_indexed_at,
            updated_at=excluded.updated_at
        """,
        (
            digest_id,
            workspace_id,
            result.file_path,
            result.file_sha1,
            result.language,
            result.chunk_count,
            result.symbol_count,
            result.purpose_short,
            json.dumps(result.top_symbols, ensure_ascii=False),
            now,
            now,
        ),
    )
    conn.commit()
    return digest_id


# ============================================================
# Worker loop
# ============================================================


def process_entry(entry: QueueEntry) -> str | None:
    """Read the source file, compute digest, UPSERT into the right DB.

    Returns the digest id on success, None on any error (file missing,
    DB unreachable, parse fail). All failures are logged but never
    abort the worker.
    """
    src_path = Path(entry.file_path)
    if not src_path.is_file():
        logger.info("digest_worker_skip_missing", extra={"file": entry.file_path})
        return None
    try:
        content = src_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning(
            "digest_worker_read_failed",
            extra={"file": entry.file_path, "error": str(exc)},
        )
        return None
    result = compute_digest(entry.file_path, content)
    try:
        conn = sqlite3.connect(entry.db_path)
        try:
            return upsert_digest(conn, workspace_id=entry.workspace_id, result=result)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning(
            "digest_worker_db_failed",
            extra={"file": entry.file_path, "db": entry.db_path, "error": str(exc)},
        )
        return None


def drain_queue(queue_path: Path | None = None) -> int:
    """One sweep: read queue, dedupe, process each entry, clear queue.

    Returns the number of digests actually upserted (failures excluded).
    Idempotent: a second call on an empty queue is a no-op returning 0.
    """
    path = queue_path or DEFAULT_QUEUE_PATH
    entries = _dedupe_entries(_read_queue(path))
    if not entries:
        return 0
    processed = 0
    for entry in entries:
        if process_entry(entry) is not None:
            processed += 1
    _clear_queue(path)
    return processed


def run_worker(
    *, queue_path: Path | None = None, poll_interval_s: float = 2.0, max_iterations: int = 0
) -> int:
    """Long-running worker loop. ``max_iterations=0`` means forever.

    The OS-level wrapper (PowerShell scheduled task) launches this with
    ``max_iterations=0``; tests pass a small integer for bounded runs.
    Returns the total digests processed across all iterations.
    """
    total = 0
    iteration = 0
    while True:
        total += drain_queue(queue_path)
        iteration += 1
        if max_iterations and iteration >= max_iterations:
            return total
        time.sleep(poll_interval_s)
