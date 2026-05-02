"""Fresh-process smoke check for the agent-memory-lite MCP handlers.

This is intentionally narrower than a full MCP protocol test. It proves that a
new stdio-server process can answer the slowest/highest-risk handler,
`memory_get_context`, without hanging on model/vector-store initialization.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from agent_memory_lite.config.settings import Settings

_HELPER = r"""
from __future__ import annotations

import json
import os
import time

from agent_memory_lite.mcp import stdio_server

payload = json.loads(os.environ["MEMORY_MCP_SMOKE_PAYLOAD"])
started = time.perf_counter()
result = stdio_server._handle_get_context(payload)
elapsed = time.perf_counter() - started
text = str(result.get("context_text", ""))
sources = result.get("sources", [])
print(json.dumps({
    "elapsed_sec": round(elapsed, 3),
    "context_chars": len(text),
    "sources_count": len(sources) if isinstance(sources, list) else 0,
    "has_memory_context": "<memory_context" in text,
    "has_behavior_instructions": "<behavior_instructions" in text,
    "has_agent_capabilities": "<agent_capabilities" in text,
    "has_active_decisions": "<active_decisions" in text,
}, ensure_ascii=False, sort_keys=True))
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-test MCP memory_get_context latency.")
    parser.add_argument("--workspace", "--workspace-id", dest="workspace", default=None)
    parser.add_argument("--db-path", "--db", dest="db_path", default=None)
    parser.add_argument("--vector-path", "--vectors", dest="vector_path", default=None)
    parser.add_argument(
        "--query",
        default="memory MCP smoke behavior instructions roles skills",
        help="Query used for memory_get_context.",
    )
    parser.add_argument("--max-tokens", type=int, default=2500)
    parser.add_argument("--max-seconds", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--require-behavior", action="store_true")
    parser.add_argument("--require-capabilities", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=None)
    updates: dict[str, Any] = {}
    if args.workspace:
        updates["workspace_id"] = args.workspace
    if args.db_path:
        updates["db_path"] = Path(args.db_path)
    if args.vector_path:
        updates["vector_db_path"] = Path(args.vector_path)
    return settings.model_copy(update=updates)


def _run_helper(settings: Settings, args: argparse.Namespace) -> dict[str, Any]:
    payload = {
        "workspace_id": settings.workspace_id,
        "query": args.query,
        "max_tokens": args.max_tokens,
    }
    env = os.environ.copy()
    env["MEMORY_MCP_SMOKE_PAYLOAD"] = json.dumps(payload, ensure_ascii=False)
    env["MEMORY_DB_PATH"] = str(settings.db_path)
    env["VECTOR_DB_PATH"] = str(settings.vector_db_path)
    env["MEMORY_WORKSPACE_ID"] = settings.workspace_id
    env.setdefault("OLLAMA_PROBE_SKIP", "true")
    env.setdefault("MCP_GET_CONTEXT_HTTP_DELEGATE", "true")

    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-c", _HELPER],
        check=False,
        capture_output=True,
        text=True,
        timeout=args.timeout_seconds,
        env=env,
    )
    wall_elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        return {
            "status": "degraded",
            "elapsed_sec": round(wall_elapsed, 3),
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "failure": f"subprocess exited {completed.returncode}",
        }
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {
            "status": "degraded",
            "elapsed_sec": round(wall_elapsed, 3),
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "failure": f"invalid helper JSON: {exc}",
        }
    result["wall_elapsed_sec"] = round(wall_elapsed, 3)
    result["status"] = "ok"
    return result


def _evaluate(result: dict[str, Any], args: argparse.Namespace) -> tuple[str, list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    if result.get("status") != "ok":
        failures.append(str(result.get("failure") or "mcp smoke helper failed"))
    if not result.get("has_memory_context"):
        failures.append("memory_get_context did not return <memory_context>")
    if (
        float(result.get("wall_elapsed_sec") or result.get("elapsed_sec") or 999.0)
        > args.max_seconds
    ):
        failures.append(f"memory_get_context exceeded {args.max_seconds:.1f}s")
    if args.require_behavior and not result.get("has_behavior_instructions"):
        failures.append("context is missing <behavior_instructions>")
    if args.require_capabilities and not result.get("has_agent_capabilities"):
        failures.append("context is missing <agent_capabilities>")
    if not result.get("has_active_decisions"):
        warnings.append("context is missing <active_decisions>")
    return ("degraded" if failures else "ok"), failures, warnings


def _print(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"status={payload['status']} workspace_id={payload['workspace_id']}")
    print("result=" + json.dumps(payload["result"], ensure_ascii=False, sort_keys=True))
    if payload["failures"]:
        print("failures=" + json.dumps(payload["failures"], ensure_ascii=False))
    if payload["warnings"]:
        print("warnings=" + json.dumps(payload["warnings"], ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = _settings(args)
    try:
        result = _run_helper(settings, args)
    except subprocess.TimeoutExpired:
        result = {
            "status": "degraded",
            "failure": f"subprocess timed out after {args.timeout_seconds:.1f}s",
        }
    status, failures, warnings = _evaluate(result, args)
    payload = {
        "status": status,
        "workspace_id": settings.workspace_id,
        "db_path": str(settings.db_path),
        "vector_path": str(settings.vector_db_path),
        "query": args.query,
        "result": result,
        "failures": failures,
        "warnings": warnings,
    }
    _print(payload, as_json=args.json)
    return 0 if status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
