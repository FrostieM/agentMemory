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
import json
import os
import sys

import httpx

DEFAULT_BASE = os.environ.get("AGENT_MEMORY_BASE", "http://127.0.0.1:8765")
DEFAULT_WORKSPACE = os.environ.get("AGENT_MEMORY_WORKSPACE", "default")
DEFAULT_MAX_TOKENS = int(os.environ.get("AGENT_MEMORY_INJECT_TOKENS", "1500"))
DEFAULT_TIMEOUT = float(os.environ.get("AGENT_MEMORY_INJECT_TIMEOUT", "30.0"))


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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--db-path", default=os.environ.get("AGENT_MEMORY_DB_PATH"))
    parser.add_argument(
        "--vector-path", default=os.environ.get("AGENT_MEMORY_VECTOR_PATH")
    )
    parser.add_argument(
        "--workspace", default=os.environ.get("AGENT_MEMORY_WORKSPACE", DEFAULT_WORKSPACE)
    )
    return parser.parse_known_args()[0]


def main() -> int:
    args = _parse_args()
    event = _read_event()
    prompt = str(event.get("prompt", "")).strip()
    if not prompt:
        return 0

    payload: dict[str, object] = {
        "workspace_id": str(event.get("workspace_id") or args.workspace),
        "query": prompt[:1000],
        "max_tokens": DEFAULT_MAX_TOKENS,
    }
    task_id = event.get("task_id") or event.get("session_id")
    if task_id:
        payload["task_id"] = str(task_id)

    headers: dict[str, str] = {}
    if args.db_path:
        headers["X-Memory-DB-Path"] = args.db_path
    if args.vector_path:
        headers["X-Memory-Vector-Path"] = args.vector_path

    try:
        response = httpx.post(
            f"{DEFAULT_BASE}/memory/get_context",
            json=payload,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        _emit_notice(
            f"agent-memory-lite unreachable at {DEFAULT_BASE} ({exc!s}). "
            "Start the service with `python -m agent_memory_lite` and the "
            "next prompt will see context."
        )
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
