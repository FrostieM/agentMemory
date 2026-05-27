"""Fresh-process smoke check for the agent-memory-lite MCP handlers.

This is intentionally narrower than a full MCP protocol test. It proves that a
new stdio-server process can answer the v3 compact MCP handlers without hanging
on model/vector-store initialization.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
for _path in (_SRC_ROOT, _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

if TYPE_CHECKING:
    from agent_memory_lite.config.settings import Settings

EXPECTED_V3_MCP_TOOLS = {
    "memory_search",
    "memory_get",
    "memory_write",
    "memory_edit",
    "memory_pin",
    "memory_archive",
    "memory_brief",
    "memory_lint",
    "memory_invoke_skill",
    "memory_impact_check",
    "memory_status",
    "memory_plan",
}

_HELPER = r"""
from __future__ import annotations

import json
import os
import time

from agent_memory_lite.mcp import stdio_handlers_memory
from agent_memory_lite.mcp.stdio_tools import ALL_TOOLS

payload = json.loads(os.environ["MEMORY_MCP_SMOKE_PAYLOAD"])
started = time.perf_counter()
brief = stdio_handlers_memory._handle_v3_brief(payload)
status = stdio_handlers_memory._handle_v3_status({
    "workspace_id": payload["workspace_id"],
    "include_environment": False,
    "include_active_memory": False,
})
elapsed = time.perf_counter() - started
brief_data = brief.get("data") if isinstance(brief, dict) else {}
status_data = status.get("data") if isinstance(status, dict) else {}
memory = status_data.get("memory", {}) if isinstance(status_data, dict) else {}
text = str(brief_data.get("body_md", "")) if isinstance(brief_data, dict) else ""
sections = brief_data.get("sections", []) if isinstance(brief_data, dict) else []
print(json.dumps({
    "elapsed_sec": round(elapsed, 3),
    "brief_chars": len(text),
    "sections": sections if isinstance(sections, list) else [],
    "has_brief": bool(brief.get("ok")) and bool(text),
    "has_behaviors": int(memory.get("behaviors_active") or 0) > 0,
    "has_agent_capabilities": int(memory.get("capabilities_active_total") or 0) > 0,
    "has_active_decisions": int(memory.get("decisions_active") or 0) > 0,
    "tool_names": sorted(t.name for t in ALL_TOOLS),
}, ensure_ascii=False, sort_keys=True))
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-test MCP v3 compact tool latency.")
    parser.add_argument("--workspace", "--workspace-id", dest="workspace", default=None)
    parser.add_argument("--db-path", "--db", dest="db_path", default=None)
    parser.add_argument("--vector-path", "--vectors", dest="vector_path", default=None)
    parser.add_argument(
        "--query",
        default="memory MCP smoke behavior instructions roles skills",
        help="Task text passed to memory_brief.",
    )
    parser.add_argument("--max-tokens", type=int, default=2500)
    parser.add_argument("--max-seconds", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--require-behavior", action="store_true")
    parser.add_argument("--require-capabilities", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    from agent_memory_lite.config.settings import Settings  # noqa: PLC0415

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
        "task": args.query,
        "max_tokens": args.max_tokens,
    }
    env = os.environ.copy()
    env["MEMORY_MCP_SMOKE_PAYLOAD"] = json.dumps(payload, ensure_ascii=False)
    env["MEMORY_DB_PATH"] = str(settings.db_path)
    env["VECTOR_DB_PATH"] = str(settings.vector_db_path)
    env["MEMORY_WORKSPACE_ID"] = settings.workspace_id
    env.setdefault("OLLAMA_PROBE_SKIP", "true")
    pythonpath = os.pathsep.join(str(p) for p in (_SRC_ROOT, _REPO_ROOT))
    env["PYTHONPATH"] = (
        pythonpath if not env.get("PYTHONPATH") else pythonpath + os.pathsep + env["PYTHONPATH"]
    )

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
    stdout = completed.stdout.strip()
    json_line = stdout.splitlines()[-1] if stdout else ""
    try:
        result = json.loads(json_line)
    except json.JSONDecodeError as exc:
        return {
            "status": "degraded",
            "elapsed_sec": round(wall_elapsed, 3),
            "stdout": stdout,
            "stderr": completed.stderr.strip(),
            "failure": f"invalid helper JSON: {exc}",
        }
    if stdout and stdout != json_line:
        result["helper_prefix_stdout"] = stdout[: -len(json_line)].strip()
    result["wall_elapsed_sec"] = round(wall_elapsed, 3)
    result["status"] = "ok"
    return result


def _evaluate(result: dict[str, Any], args: argparse.Namespace) -> tuple[str, list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    if result.get("status") != "ok":
        failures.append(str(result.get("failure") or "mcp smoke helper failed"))
    if not result.get("has_brief"):
        failures.append("memory_brief did not return a compact brief")
    if (
        float(result.get("wall_elapsed_sec") or result.get("elapsed_sec") or 999.0)
        > args.max_seconds
    ):
        failures.append(f"memory_brief/status exceeded {args.max_seconds:.1f}s")
    if args.require_behavior and not result.get("has_behaviors"):
        failures.append("workspace has no active behaviors")
    if args.require_capabilities and not result.get("has_agent_capabilities"):
        failures.append("workspace has no active capabilities")
    tool_names = result.get("tool_names")
    if isinstance(tool_names, list):
        actual = {str(name) for name in tool_names}
        missing = sorted(EXPECTED_V3_MCP_TOOLS - actual)
        extra = sorted(actual - EXPECTED_V3_MCP_TOOLS)
        if missing or extra:
            failures.append(
                f"MCP tool surface mismatch: missing={missing or []}; extra={extra or []}"
            )
    else:
        failures.append("MCP helper did not report tool_names")
    if not result.get("has_active_decisions"):
        warnings.append("workspace has no active decisions")
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
