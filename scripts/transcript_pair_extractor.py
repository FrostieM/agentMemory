#!/usr/bin/env python3
"""Read Claude Code's session JSONL and find the most recent assistant turn.

The UserPromptSubmit hook (``inject_memory_context.py``) calls
``find_last_assistant_text(transcript_path)`` to retrieve the agent's
last text-only claim within a recent time window. The hook then pairs
that claim with the current user prompt; if the pair fits the
"agent claim → user correction" heuristic
(``extraction/correction_patterns.py``), both are ingested as episodes
so the CorrectionExtractor can propose a behavior fix.

Invariants:
* Read-only — never writes to the transcript.
* Best-effort — returns ``None`` on any parse error rather than raising,
  so the hook degrades gracefully if Claude Code's JSONL format drifts.
* Bounded — only reads the tail of the file (default 400 lines).
* Filters out tool_use / tool_result blocks; only pure text claims count.

The function is callable from ``inject_memory_context.py`` as a library
import; it has no side effects when imported.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_DEFAULT_TAIL_LINES = 400


@dataclass(frozen=True, slots=True)
class AssistantTurn:
    """A located assistant turn with text content and metadata."""

    text: str
    timestamp: str  # ISO-8601 string, exactly as in the JSONL
    uuid: str  # transcript line uuid (for episode metadata lineage)


def find_last_assistant_text(
    transcript_path: str | Path,
    *,
    now: datetime | None = None,
    window_minutes: int = 30,
    tail_lines: int = _DEFAULT_TAIL_LINES,
) -> AssistantTurn | None:
    """Return the most recent assistant text turn within the window.

    Returns ``None`` when:
    * the file does not exist or cannot be read,
    * no assistant turn with a text block was found in the tail,
    * the latest assistant turn is older than ``window_minutes``.

    The function never raises; all exceptions are swallowed and
    converted to ``None``.
    """
    try:
        # Expand ~ so Claude Code paths like "~/.claude/projects/..."
        # resolve. Without this, Path("~/...").exists() is False on
        # every OS and the hook silently skips correction capture.
        path = Path(str(transcript_path)).expanduser()
        if not path.exists() or not path.is_file():
            return None
        lines = _read_tail(path, tail_lines)
    except (OSError, ValueError):
        return None

    cutoff = (now or datetime.now(UTC)) - timedelta(minutes=window_minutes)

    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") != "assistant":
            continue
        text, uuid, ts = _extract_text_block(obj)
        if not text:
            continue
        # Time-window filter
        ts_parsed = _parse_iso(ts)
        if ts_parsed is None:
            continue
        if ts_parsed < cutoff:
            return None  # older than window — stop searching
        return AssistantTurn(text=text, timestamp=ts, uuid=uuid)

    return None


def _read_tail(path: Path, tail_lines: int) -> list[str]:
    """Read the last ``tail_lines`` lines of a UTF-8 file efficiently.

    For a 20k-line file this avoids loading the entire content; we
    stream from the file end via a fixed-size byte buffer and split.
    Fallback for very small files just reads them whole.
    """
    size = path.stat().st_size
    if size <= 64 * 1024:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return f.readlines()[-tail_lines:]

    chunk_size = 64 * 1024
    accumulated = bytearray()
    with path.open("rb") as f:
        pos = size
        while pos > 0 and accumulated.count(b"\n") <= tail_lines:
            read = min(chunk_size, pos)
            pos -= read
            f.seek(pos)
            accumulated[:0] = f.read(read)
    text = accumulated.decode("utf-8", errors="replace")
    return text.splitlines()[-tail_lines:]


def _extract_text_block(obj: dict) -> tuple[str, str, str]:
    """Pull (text, uuid, timestamp) from a Claude Code assistant entry.

    Claude Code's ``message.content`` is a list of typed blocks. We
    pick the FIRST ``type=text`` block we find with non-empty text;
    tool_use and tool_result blocks are skipped (they're not claims
    the user can correct).
    """
    uuid = str(obj.get("uuid") or "")
    ts = str(obj.get("timestamp") or "")
    msg = obj.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip(), uuid, ts
    if not isinstance(content, list):
        return "", uuid, ts
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip(), uuid, ts
    return "", uuid, ts


def _parse_iso(ts: str) -> datetime | None:
    """Parse an ISO-8601 timestamp; tolerate trailing ``Z`` and missing tz."""
    if not ts:
        return None
    try:
        cleaned = ts.rstrip("Z")
        # Standardize so fromisoformat handles it; append +00:00 when no tz.
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        elif "+" not in cleaned[-6:] and "-" not in cleaned[-6:]:
            cleaned = cleaned + "+00:00"
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return None
