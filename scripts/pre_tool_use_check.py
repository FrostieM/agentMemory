"""Claude Code PreToolUse hook: enforce active workspace rules.

Wired into Claude Code's hook system, this runs BEFORE every tool
invocation. It reads the tool name + payload from stdin, resolves the
workspace for ``cwd``, loads active enforcement-tagged behavior
instructions, and routes them through ``enforcement.dispatch.decide``.

Exit codes:

* ``0`` — allow the tool call.
* ``2`` — block the tool call; stderr is shown to the model so it can
  fix the violation and retry.

Failure-soft: any unexpected error (missing workspace, IO error,
import failure) returns exit 0 so a buggy hook never deadlocks the
agent. The trade-off is that a broken hook degrades to "no
enforcement" instead of "every tool call denied".

Configure in ``~/.claude/settings.json``:

    {
      "hooks": {
        "PreToolUse": [{
          "matcher": "Read|Edit|Write|NotebookEdit|Grep|Bash|mcp__agent-memory-lite__memory_.*",
          "hooks": [{
            "type": "command",
            "command": "<venv-python> <repo>/scripts/pre_tool_use_check.py"
          }]
        }]
      }
    }

``scripts/setup_agent.py`` writes that snippet for you.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from hook_audit import record_hook_event
except ImportError:  # pragma: no cover - used when tests import scripts as a package
    from scripts.hook_audit import record_hook_event

with contextlib.suppress(AttributeError, ValueError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
with contextlib.suppress(AttributeError, ValueError):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


_BYPASS_FLAGS = {"1", "true", "yes", "on"}
_DEFAULT_REGISTRY = (
    Path(os.environ["MEMORY_WORKSPACES_FILE"])
    if os.environ.get("MEMORY_WORKSPACES_FILE")
    else Path.home() / ".agent_memory" / "workspaces.json"
)
_DEBUG_LOG_ENV = "MEMORY_PRETOOLUSE_DEBUG"


def _env_values(*names: str) -> list[str]:
    """Return non-empty env values in priority order, without duplicates."""
    values: list[str] = []
    seen: set[str] = set()
    for name in names:
        value = os.environ.get(name, "").strip()
        if value and value not in seen:
            values.append(value)
            seen.add(value)
    return values


def _bypass_enabled() -> bool:
    return os.environ.get("MEMORY_SKIP_PRETOOLUSE_CHECK", "").strip().lower() in _BYPASS_FLAGS


def _debug_log(*, tool_name: str, workspace_id: str, decision: str) -> None:
    """Append one tab-separated invocation row when MEMORY_PRETOOLUSE_DEBUG is set.

    Opt-in trace so the operator can confirm the hook is firing and see
    its decisions per tool call. Any IO failure is swallowed — the hook
    must never crash because the debug log is misconfigured.
    """
    target = os.environ.get(_DEBUG_LOG_ENV, "").strip()
    if not target:
        return
    try:
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        line = f"{stamp}\t{tool_name}\t{workspace_id}\t{decision}\n"
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        with Path(target).open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        return


def _load_registry_entries() -> list[dict[str, Any]]:
    if not _DEFAULT_REGISTRY.exists():
        return []
    try:
        payload = json.loads(_DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = payload.get("workspaces") if isinstance(payload, dict) else None
    return entries if isinstance(entries, list) else []


def _safe_registry_path(raw: str) -> str:
    """Round-2 audit: reject registry db_path shapes that are never a
    legitimate local DB path — UNC / network paths, null bytes,
    non-absolute paths. The registry file is the trust root, so this
    only blocks the unambiguously-malicious shapes; a rejected value
    returns '' so the caller treats the entry as unroutable."""
    raw = (raw or "").strip()
    if not raw or "\x00" in raw:
        return ""
    if raw.startswith(("\\\\", "//")):  # UNC / network share
        return ""
    if not os.path.isabs(raw):
        return ""
    return raw


def _resolve_workspace(cwd: str) -> tuple[str, str] | None:
    """Walk up from ``cwd`` until a registered project_root matches.

    Returns ``(workspace_id, db_path)`` or None if nothing matches. An
    explicit ``MEMORY_WORKSPACE_ID`` env var overrides the lookup when
    the registry holds a matching id. ``AGENT_MEMORY_WORKSPACE`` remains
    a legacy fallback.
    """
    entries = _load_registry_entries()
    if not entries:
        return None
    for explicit in _env_values("MEMORY_WORKSPACE_ID", "AGENT_MEMORY_WORKSPACE"):
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id") == explicit:
                db = _safe_registry_path(str(entry.get("db_path", "")))
                if db:
                    return explicit, db
    target = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    for parent in [target, *target.parents]:
        target_str = str(parent).rstrip("\\/").casefold()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            root = str(entry.get("project_root", "")).rstrip("\\/").casefold()
            if root and root == target_str:
                wid = str(entry.get("id", ""))
                db = _safe_registry_path(str(entry.get("db_path", "")))
                if wid and db:
                    return wid, db
    return None


def _read_event() -> dict[str, Any] | None:
    raw = sys.stdin.read()
    if not raw.strip():
        return None
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _decide_for_event(event: dict[str, Any]) -> tuple[bool, str, str, str]:  # noqa: PLR0911 - guard chain
    """Resolve workspace + run dispatch.decide.

    Returns ``(allow, diagnostic, workspace_id, db_path)``.
    ``workspace_id`` is ``"-"`` when no registry match was found, so
    the debug log can record both "hook ran but no workspace" and
    "hook ran for ws=X". Every failure path along the way collapses to
    a fail-open allow.
    """
    tool_name = event.get("tool_name")
    tool_input = event.get("tool_input") or {}
    transcript_path = event.get("transcript_path")
    cwd = event.get("cwd") or os.getcwd()
    if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
        return True, "", "-", ""
    resolved = _resolve_workspace(cwd)
    if resolved is None:
        return True, "", "-", ""
    workspace_id, db_path = resolved
    if not Path(db_path).exists():
        return True, "", workspace_id, db_path
    try:
        from agent_memory_lite.enforcement.dispatch import decide  # noqa: PLC0415
        from agent_memory_lite.enforcement.session_trail import (  # noqa: PLC0415
            read_prior_tool_calls,
        )
    except ImportError:
        return True, "", workspace_id, db_path
    trail = read_prior_tool_calls(transcript_path)
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error:
        return True, "", workspace_id, db_path
    try:
        decision = decide(
            conn,
            workspace_id=workspace_id,
            tool_name=tool_name,
            tool_input=tool_input,
            trail=trail,
            ollama_base_url=os.environ.get("OLLAMA_BASE_URL"),
            ollama_model=os.environ.get("OLLAMA_MODEL"),
        )
    except Exception:
        # Any decision-path failure is fail-OPEN. The hook protects
        # against memory-mismatches, not safety boundaries; crashing on
        # ImportError/OSError/AttributeError would block the user's
        # tool call with a stderr traceback that Claude Code may or
        # may not handle gracefully. Better to allow the call and let
        # the agent / operator notice memory is degraded.
        return True, "", workspace_id, db_path
    finally:
        conn.close()
    return decision.allow, decision.diagnostic, workspace_id, db_path


def main() -> int:
    if _bypass_enabled():
        _debug_log(tool_name="-", workspace_id="-", decision="bypass")
        record_hook_event(
            hook="PreToolUse",
            event={},
            workspace_id="-",
            status="skip",
            detail="bypass",
        )
        return 0
    event = _read_event()
    if event is None:
        _debug_log(tool_name="-", workspace_id="-", decision="no_event")
        record_hook_event(
            hook="PreToolUse",
            event={},
            workspace_id="-",
            status="skip",
            detail="no_event",
        )
        return 0
    tool_name = str(event.get("tool_name") or "-")
    allow_call, diagnostic, workspace_id, db_path = _decide_for_event(event)
    decision_label = "allow" if allow_call else "block"
    _debug_log(tool_name=tool_name, workspace_id=workspace_id, decision=decision_label)
    record_hook_event(
        hook="PreToolUse",
        event=event,
        workspace_id=workspace_id,
        status=decision_label,
        db_path=db_path,
        detail=diagnostic[:500] if diagnostic else "",
    )
    if allow_call:
        return 0
    print(diagnostic, file=sys.stderr)
    return 2


def _safe_main() -> int:
    """v3.0.0-final: the hook MUST NOT crash with a traceback.

    Claude Code runs this script on every PreToolUse. A bare exception
    here propagates a non-zero exit + stderr traceback to the user,
    which the runtime may treat as a tool-call block. The hook's only
    job is to advise; a degraded memory layer should never break the
    operator's workflow. Catch every unexpected exception, log it
    quietly to the debug log if AGENT_MEMORY_HOOK_DEBUG=1, and exit 0
    so the tool call proceeds.
    """
    try:
        return main()
    except Exception as exc:
        with contextlib.suppress(Exception):
            _debug_log(tool_name="-", workspace_id="-", decision=f"crash:{type(exc).__name__}")
            record_hook_event(
                hook="PreToolUse",
                event={},
                workspace_id="-",
                status="crash",
                detail=type(exc).__name__,
            )
        return 0


if __name__ == "__main__":
    raise SystemExit(_safe_main())
