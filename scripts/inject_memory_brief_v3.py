"""v3 UserPromptSubmit hook: prepend a ≤500-token brief from /v3/memory/brief.

The v3 replacement for ``scripts/inject_memory_context.py``. Where the v2 hook
emits a verbose ``<memory_context>`` envelope (~1500 tokens) by calling
``/memory/get_context``, this v3 hook emits a tight pre-task brief composed
from compact projections (~500 tokens) by calling ``/v3/memory/brief``.

Architecture:

* The v2 hook keeps shipping for projects still on v2.
* This v3 hook is opt-in via setup_agent.py ``--v3`` (added at week 5)
  or by manually editing ``~/.claude/settings.json``.
* At week 8 cutover, setup_agent flips new projects to this hook by
  default.

Stdin: a Claude Code event JSON with at least ``{"prompt": "<text>"}``.
Other fields (``cwd``, ``session_id``, ``workspace_id``) are forwarded
into workspace resolution when present.

Stdout: a single block

    <agent-memory>
    <memory_brief>
      ...500-token body_md...
    </memory_brief>
    </agent-memory>

Failure-soft: any HTTP / JSON / FS error emits a one-line notice and exits
0 so the agent still sees the user's prompt.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path

import httpx

# Force UTF-8 stdout so the brief block is never truncated on Windows.
with contextlib.suppress(AttributeError, ValueError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
with contextlib.suppress(AttributeError, ValueError):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


DEFAULT_BASE = os.environ.get("AGENT_MEMORY_BASE", "http://127.0.0.1:8765")
DEFAULT_WORKSPACE = os.environ.get("AGENT_MEMORY_WORKSPACE", "")
DEFAULT_MAX_TOKENS = int(os.environ.get("AGENT_MEMORY_BRIEF_TOKENS", "500"))
DEFAULT_TIMEOUT = float(os.environ.get("AGENT_MEMORY_BRIEF_TIMEOUT", "10.0"))
REGISTRY_PATH = (
    Path(os.environ["MEMORY_WORKSPACES_FILE"])
    if os.environ.get("MEMORY_WORKSPACES_FILE")
    else Path.home() / ".agent_memory" / "workspaces.json"
)


def _read_event() -> dict[str, object]:
    """Read the JSON event Claude Code writes to stdin. Tolerates empty stdin."""
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _list_registry() -> list[dict[str, object]]:
    if not REGISTRY_PATH.exists():
        return []
    try:
        payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    entries = payload.get("workspaces")
    return entries if isinstance(entries, list) else []


def _resolve_workspace_from_cwd(cwd: Path) -> tuple[str, str, str]:
    """Walk cwd parents; return (workspace_id, db_path, vector_path) on hit, else empty strings."""
    entries = _list_registry()
    if not entries:
        return ("", "", "")
    candidate = cwd.resolve()
    for parent in [candidate, *candidate.parents]:
        target = str(parent).rstrip("\\/").casefold()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            root = str(entry.get("project_root", "")).rstrip("\\/").casefold()
            if root and root == target:
                return (
                    str(entry.get("id", "")),
                    str(entry.get("db_path", "")),
                    str(entry.get("vector_path", "")),
                )
    return ("", "", "")


def _emit_brief(body_md: str) -> None:
    sys.stdout.write("<agent-memory>\n<memory_brief>\n")
    sys.stdout.write(body_md)
    if not body_md.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.write("</memory_brief>\n</agent-memory>\n")


def _emit_notice(message: str) -> None:
    sys.stdout.write(f"<agent-memory>\n<!-- v3 brief hook notice: {message} -->\n</agent-memory>\n")


def _fetch_brief(
    *, base_url: str, workspace_id: str, max_tokens: int, headers: dict[str, str]
) -> dict[str, object] | None:
    """Call /v3/memory/brief. Returns the envelope ``data`` payload or None on error."""
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/v3/memory/brief",
            params={"workspace_id": workspace_id, "max_tokens": max_tokens},
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    try:
        body = response.json()
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict) or not body.get("ok"):
        return None
    data = body.get("data")
    return data if isinstance(data, dict) else None


def main() -> int:
    event = _read_event()
    prompt = str(event.get("prompt", "")).strip()
    if not prompt:
        return 0

    workspace = str(event.get("workspace_id") or DEFAULT_WORKSPACE or "")
    db_path = os.environ.get("AGENT_MEMORY_DB_PATH", "")
    vector_path = os.environ.get("AGENT_MEMORY_VECTOR_PATH", "")

    if not workspace or not db_path:
        cwd_candidates: list[Path] = []
        event_cwd = event.get("cwd")
        if event_cwd:
            cwd_candidates.append(Path(str(event_cwd)))
        with contextlib.suppress(OSError):
            cwd_candidates.append(Path(os.getcwd()))
        for candidate in cwd_candidates:
            ws, db, vec = _resolve_workspace_from_cwd(candidate)
            if ws:
                workspace = workspace or ws
                db_path = db_path or db
                vector_path = vector_path or vec
                break

    if not workspace:
        _emit_notice(
            "no workspace registered for this cwd. "
            "Run `python scripts/setup_agent.py --project <path>` "
            "from the agent-memory-lite repo."
        )
        return 0

    headers: dict[str, str] = {}
    if db_path:
        headers["X-Memory-DB-Path"] = db_path
    if vector_path:
        headers["X-Memory-Vector-Path"] = vector_path

    data = _fetch_brief(
        base_url=DEFAULT_BASE,
        workspace_id=workspace,
        max_tokens=DEFAULT_MAX_TOKENS,
        headers=headers,
    )
    if data is None:
        _emit_notice(
            f"agent-memory-lite v3 brief unreachable at {DEFAULT_BASE}. "
            "Run `python -m agent_memory_lite` to start the service."
        )
        return 0

    body_md = data.get("body_md")
    if not isinstance(body_md, str) or not body_md.strip():
        _emit_notice("v3 brief returned no body — empty workspace?")
        return 0

    _emit_brief(body_md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
