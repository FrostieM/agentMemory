"""Best-effort JSONL audit trail for Claude/Codex memory hooks."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def record_hook_event(
    *,
    hook: str,
    event: dict[str, Any] | dict[str, object],
    workspace_id: str,
    status: str,
    db_path: str = "",
    detail: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """Append one local audit row without ever breaking hook execution."""
    try:
        path = _audit_path(event=event, db_path=db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "hook": hook,
            "workspace_id": workspace_id or "-",
            "status": status,
            "detail": detail,
            "cwd": str(event.get("cwd") or ""),
            "session_id": str(event.get("session_id") or ""),
            "transcript_path": str(event.get("transcript_path") or ""),
        }
        if tool_name := event.get("tool_name"):
            row["tool_name"] = str(tool_name)
        if "prompt" in event:
            row["prompt_chars"] = len(str(event.get("prompt") or ""))
        if extra:
            row["extra"] = extra
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        return


def _audit_path(*, event: dict[str, Any] | dict[str, object], db_path: str) -> Path:
    explicit = os.environ.get("MEMORY_HOOK_AUDIT_LOG", "").strip()
    if explicit:
        return Path(explicit)
    if db_path:
        return Path(db_path).expanduser().resolve().parent / "hook_events.jsonl"
    cwd = Path(str(event.get("cwd") or os.getcwd())).expanduser().resolve()
    return cwd / ".agent_memory" / "hook_events.jsonl"
