"""Claude Code UserPromptSubmit hook: prepend agent-memory-lite context.

Wired into Claude Code's hook system, this runs before each user message
reaches the agent. It calls /memory/get_context (or the in-process MCP
service), wraps the response in a plain-text preamble, and emits stdout
that Claude Code injects ahead of the user's prompt.

Stdin: a JSON object emitted by Claude Code with at least
    {"prompt": "<user text>"}
Other Claude Code fields (session_id, cwd, etc.) are tolerated and
forwarded into the memory query when present.

Stdout: a single block

    <agent-memory>
    <memory_context>
      ...
    </memory_context>
    </agent-memory>

This block is what Claude Code prepends to the user's message. The block
is intentionally token-bounded (default 1500 tokens of memory). On any
failure (service down, JSON malformed, network error) the hook prints a
short notice and exits 0 so the user's prompt still reaches the agent.

Configure in `~/.claude/settings.json`:

    {
      "hooks": {
        "UserPromptSubmit": [{
          "hooks": [{
            "type": "command",
            "command": "<venv-python> <repo>/scripts/inject_memory_context.py"
          }]
        }]
      }
    }

`scripts/setup_agent.py` writes that snippet for you.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx

DEFAULT_BASE = os.environ.get("AGENT_MEMORY_BASE", "http://127.0.0.1:8765")
DEFAULT_WORKSPACE = os.environ.get("AGENT_MEMORY_WORKSPACE", "default")
DEFAULT_MAX_TOKENS = int(os.environ.get("AGENT_MEMORY_INJECT_TOKENS", "1500"))
DEFAULT_TIMEOUT = float(os.environ.get("AGENT_MEMORY_INJECT_TIMEOUT", "30.0"))
DEFAULT_DEDUPE_TTL_SECONDS = float(os.environ.get("AGENT_MEMORY_HOOK_DEDUPE_TTL", "2.0"))
DEFAULT_REGISTRY = (
    Path(os.environ["MEMORY_WORKSPACES_FILE"])
    if os.environ.get("MEMORY_WORKSPACES_FILE")
    else Path.home() / ".agent_memory" / "workspaces.json"
)


def _resolve_from_registry(cwd: Path) -> dict[str, str] | None:
    """Walk up from cwd; if a parent matches a registered project_root, return it.

    Returns a dict with `workspace_id`, `db_path`, `vector_path` ready for
    the hook payload. Lets a single global hook auto-route to the right
    project memory without any per-project --workspace arg.
    """
    if not DEFAULT_REGISTRY.exists():
        return None
    try:
        payload = json.loads(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entries = payload.get("workspaces") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return None
    candidate = cwd.resolve()
    for parent in [candidate, *candidate.parents]:
        target = str(parent).rstrip("\\/")
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            project_root = str(entry.get("project_root", "")).rstrip("\\/")
            if project_root and project_root.casefold() == target.casefold():
                return {
                    "workspace_id": str(entry.get("id", "")),
                    "db_path": str(entry.get("db_path", "")),
                    "vector_path": str(entry.get("vector_path", "")),
                }
    return None


def _read_event() -> dict[str, object]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"prompt": raw}
    return parsed if isinstance(parsed, dict) else {"prompt": str(parsed)}


def _emit_notice(message: str) -> None:
    sys.stdout.write(f"<agent-memory>\n<!-- memory hook notice: {message} -->\n</agent-memory>\n")


def _emit_context(context_text: str) -> None:
    sys.stdout.write("<agent-memory>\n")
    sys.stdout.write(context_text)
    if not context_text.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.write("</agent-memory>\n")


def _dedupe_cache_path() -> Path:
    raw = os.environ.get("AGENT_MEMORY_HOOK_DEDUPE_PATH")
    if raw:
        return Path(raw)
    return Path(tempfile.gettempdir()) / "agent_memory_lite_hook_dedupe.json"


def _dedupe_key(event: dict[str, object], *, workspace: str, prompt: str) -> str:
    session = str(event.get("session_id") or event.get("transcript_path") or "")
    cwd = str(event.get("cwd") or "")
    payload = "\n".join([workspace, session, cwd, prompt])
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def _should_emit_context(
    event: dict[str, object],
    *,
    workspace: str,
    prompt: str,
    cache_path: Path | None = None,
    ttl_seconds: float = DEFAULT_DEDUPE_TTL_SECONDS,
) -> bool:
    if ttl_seconds <= 0:
        return True
    key = _dedupe_key(event, workspace=workspace, prompt=prompt)
    cache_path = cache_path or _dedupe_cache_path()
    current_time = time.time()
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache = {}
    if not isinstance(cache, dict):
        cache = {}

    previous = cache.get(key)
    if isinstance(previous, int | float) and current_time - float(previous) < ttl_seconds:
        return False

    cache[key] = current_time
    cutoff = current_time - max(ttl_seconds * 4, 10.0)
    compacted = {
        str(item_key): float(item_value)
        for item_key, item_value in cache.items()
        if isinstance(item_value, int | float) and float(item_value) >= cutoff
    }
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(compacted, sort_keys=True), encoding="utf-8")
    except OSError:
        return True
    return True


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--db-path", default=os.environ.get("AGENT_MEMORY_DB_PATH"))
    parser.add_argument("--vector-path", default=os.environ.get("AGENT_MEMORY_VECTOR_PATH"))
    parser.add_argument(
        "--workspace", default=os.environ.get("AGENT_MEMORY_WORKSPACE", DEFAULT_WORKSPACE)
    )
    return parser.parse_known_args()[0]


def main() -> int:  # noqa: PLR0911, PLR0912, PLR0915 - linear hook flow with explicit early returns per failure mode
    args = _parse_args()
    event = _read_event()
    prompt = str(event.get("prompt", "")).strip()
    if not prompt:
        return 0

    db_path = args.db_path
    vector_path = args.vector_path
    workspace = str(event.get("workspace_id") or args.workspace or "")

    # cwd auto-detect: when no per-project flags are present, walk the
    # registry to find which project root we are currently in. This is what
    # makes a single global hook route correctly across many projects.
    # The event may not carry a `cwd` field (some hosts strip it), so we
    # fall back to the hook process's own cwd, which is normally the chat
    # session's working directory.
    needs_resolve = not db_path or workspace in {"", "default"}
    if needs_resolve:
        cwd_candidates: list[Path] = []
        event_cwd = event.get("cwd")
        if event_cwd:
            cwd_candidates.append(Path(str(event_cwd)))
        with contextlib.suppress(OSError):
            cwd_candidates.append(Path(os.getcwd()))
        for candidate in cwd_candidates:
            resolved = _resolve_from_registry(candidate)
            if resolved:
                if not db_path and resolved["db_path"]:
                    db_path = resolved["db_path"]
                if not vector_path and resolved["vector_path"]:
                    vector_path = resolved["vector_path"]
                if workspace in {"", "default"} and resolved["workspace_id"]:
                    workspace = resolved["workspace_id"]
                break

    if not workspace:
        workspace = DEFAULT_WORKSPACE

    # If we still ended up on `default` and no DB path was supplied, the
    # working directory is not in the hub registry. Emit a one-line notice
    # and skip the HTTP request entirely — the service would reject it as
    # MEMORY_FORBID_DEFAULT_WORKSPACE anyway, and sending it would fill the
    # log with workspace=default failures.
    if workspace == "default" and not db_path:
        cwd_now = os.getcwd()
        _emit_notice(
            f"agent-memory-lite has no workspace registered for cwd={cwd_now!r}. "
            "From the agent-memory-lite repo run "
            "`python scripts/setup_agent.py --project <path>` to register this "
            "project, or pass --workspace/--db-path in the hook command."
        )
        return 0

    if os.environ.get("AGENT_MEMORY_HOOK_DEBUG", "").strip().lower() in {"1", "true", "yes"}:
        debug_path = Path(tempfile.gettempdir()) / "agent_memory_lite_hook_debug.log"
        with contextlib.suppress(OSError):
            debug_path.write_text(
                json.dumps(
                    {
                        "ts": time.time(),
                        "cwd": os.getcwd(),
                        "event_keys": sorted(event.keys()),
                        "event_cwd": event.get("cwd"),
                        "args_db": args.db_path,
                        "args_workspace": args.workspace,
                        "resolved_workspace": workspace,
                        "resolved_db": db_path,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

    if not _should_emit_context(event, workspace=workspace, prompt=prompt):
        return 0

    payload: dict[str, object] = {
        "workspace_id": workspace,
        "query": prompt[:1000],
        "max_tokens": DEFAULT_MAX_TOKENS,
    }
    task_id = event.get("task_id") or event.get("session_id")
    if task_id:
        payload["task_id"] = str(task_id)

    headers: dict[str, str] = {}
    if db_path:
        headers["X-Memory-DB-Path"] = db_path
    if vector_path:
        headers["X-Memory-Vector-Path"] = vector_path

    try:
        response = httpx.post(
            f"{DEFAULT_BASE}/memory/get_context",
            json=payload,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
    except httpx.ConnectError:
        _emit_notice(
            f"agent-memory-lite is not running on {DEFAULT_BASE}. Start it "
            "with `python scripts/serve.py` from the agent-memory-lite repo "
            "(hub mode auto-enables when a project registry exists). The "
            "next prompt will see context."
        )
        return 0
    except httpx.TimeoutException:
        _emit_notice(
            f"agent-memory-lite at {DEFAULT_BASE} did not answer within "
            f"{DEFAULT_TIMEOUT:.0f}s. The service is probably warming embedders "
            "or applying migrations; the next prompt should succeed."
        )
        return 0
    except httpx.HTTPError as exc:
        _emit_notice(
            f"agent-memory-lite request to {DEFAULT_BASE} failed: {exc!s}. "
            "Check `curl http://127.0.0.1:8765/health` and the service "
            "console; the next prompt will retry."
        )
        return 0

    if response.status_code == 400:
        body_text = response.text[:300].replace("\n", " ")
        _emit_notice(
            f"agent-memory-lite rejected workspace_id={workspace!r} (400). "
            "The service is in strict mode but this hook routes via "
            "X-Memory-DB-Path. Restart the service with hub mode enabled: "
            "from agent-memory-lite repo run `python scripts/serve.py --hub` "
            f"or set MEMORY_HUB_MODE=true. Server detail: {body_text}"
        )
        return 0
    if response.status_code >= 400:
        body_text = response.text[:200].replace("\n", " ")
        _emit_notice(f"agent-memory-lite returned HTTP {response.status_code}: {body_text}")
        return 0

    try:
        body = response.json()
    except json.JSONDecodeError:
        _emit_notice("agent-memory-lite returned non-JSON")
        return 0

    text = body.get("context_text")
    if not isinstance(text, str) or not text.strip():
        _emit_notice("agent-memory-lite returned no context")
        return 0

    _emit_context(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
