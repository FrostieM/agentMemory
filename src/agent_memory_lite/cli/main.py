"""memory-cli entrypoint.

Mirrors the 8 HTTP endpoints. JSON to stdout; exit 1 on
``envelope.ok == false``. Defaults: ``--base-url
http://127.0.0.1:8765``, ``--workspace`` from ``AGENT_MEMORY_WORKSPACE``
env, ``--timeout 30``.

Subcommands:
    brief, list, get, count, search, write, edit, pin,
    archive, lint, skill, rollback, versions

Examples:
    memory-cli brief --workspace=agentLight
    memory-cli search "kelly" --limit=5 --workspace=copyBot
    memory-cli get --kind=decision --id=dec_x --workspace=agentLight
    cat payload.json | memory-cli write --kind=decision --stdin --workspace=agentLight
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

DEFAULT_BASE_URL = os.environ.get("AGENT_MEMORY_BASE", "http://127.0.0.1:8765")
DEFAULT_WORKSPACE = os.environ.get("AGENT_MEMORY_WORKSPACE", "")
DEFAULT_TIMEOUT = float(os.environ.get("AGENT_MEMORY_TIMEOUT", "30.0"))


def _print_envelope(env: dict[str, Any], text_mode: bool = False) -> int:
    """Render envelope to stdout. Returns exit code."""
    if text_mode and env.get("ok") and isinstance(env.get("data"), dict):
        body = env["data"].get("body_md") or env["data"].get("content")
        if isinstance(body, str):
            sys.stdout.write(body + "\n")
            return 0
    sys.stdout.write(json.dumps(env, ensure_ascii=False, indent=2) + "\n")
    return 0 if env.get("ok") else 1


def _request(
    method: str, path: str, *, base_url: str, timeout: float, **kwargs: Any
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": {"code": "transport", "message": str(exc)}}
    try:
        body: dict[str, Any] = response.json()
        return body
    except (json.JSONDecodeError, ValueError):
        return {
            "ok": False,
            "error": {
                "code": f"http_{response.status_code}",
                "message": response.text[:500],
            },
        }


# ============================================================
# Subcommand handlers
# ============================================================


def cmd_brief(args: argparse.Namespace) -> int:
    params: dict[str, Any] = {"workspace_id": args.workspace, "max_tokens": args.max_tokens}
    if args.task:
        params["task"] = args.task
    env = _request(
        "GET",
        "/memory/brief",
        base_url=args.base_url,
        timeout=args.timeout,
        params=params,
    )
    return _print_envelope(env, text_mode=args.text)


def cmd_list(args: argparse.Namespace) -> int:
    params: dict[str, Any] = {
        "workspace_id": args.workspace,
        "kind": args.kind,
        "limit": args.limit,
    }
    if args.pinned_only:
        params["pinned_only"] = True
    if args.status:
        params["status"] = args.status
    env = _request(
        "GET",
        "/memory/list",
        base_url=args.base_url,
        timeout=args.timeout,
        params=params,
    )
    return _print_envelope(env)


def cmd_get(args: argparse.Namespace) -> int:
    params: dict[str, Any] = {
        "workspace_id": args.workspace,
        "kind": args.kind,
        "id": args.id,
    }
    if args.fields:
        params["fields"] = args.fields
    env = _request(
        "GET",
        "/memory/get",
        base_url=args.base_url,
        timeout=args.timeout,
        params=params,
    )
    return _print_envelope(env, text_mode=args.text)


def cmd_count(args: argparse.Namespace) -> int:
    params: dict[str, Any] = {"workspace_id": args.workspace, "kind": args.kind}
    if args.pinned_only:
        params["pinned_only"] = True
    if args.status:
        params["status"] = args.status
    env = _request(
        "GET",
        "/memory/count",
        base_url=args.base_url,
        timeout=args.timeout,
        params=params,
    )
    return _print_envelope(env)


def cmd_search(args: argparse.Namespace) -> int:
    body: dict[str, Any] = {
        "workspace_id": args.workspace,
        "query": args.query,
        "limit": args.limit,
    }
    if args.kinds:
        body["kinds"] = args.kinds.split(",")
    env = _request(
        "POST",
        "/memory/search",
        base_url=args.base_url,
        timeout=args.timeout,
        json=body,
    )
    return _print_envelope(env)


def _read_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.stdin:
        raw = sys.stdin.read()
        if raw.strip():
            parsed: dict[str, Any] = json.loads(raw)
            return parsed
        return {}
    if args.payload:
        parsed_inline: dict[str, Any] = json.loads(args.payload)
        return parsed_inline
    return {}


def cmd_write(args: argparse.Namespace) -> int:
    body = {
        "workspace_id": args.workspace,
        "kind": args.kind,
        "payload": _read_payload(args),
        "agent_id": args.agent_id,
    }
    env = _request(
        "POST",
        "/memory/write",
        base_url=args.base_url,
        timeout=args.timeout,
        json=body,
    )
    return _print_envelope(env)


def cmd_edit(args: argparse.Namespace) -> int:
    body = {
        "workspace_id": args.workspace,
        "kind": args.kind,
        "id": args.id,
        "fields": _read_payload(args),
        "agent_id": args.agent_id,
    }
    env = _request(
        "POST",
        "/memory/edit",
        base_url=args.base_url,
        timeout=args.timeout,
        json=body,
    )
    return _print_envelope(env)


def cmd_pin(args: argparse.Namespace) -> int:
    body = {
        "workspace_id": args.workspace,
        "kind": args.kind,
        "id": args.id,
        "pinned": not args.unpin,
        "agent_id": args.agent_id,
    }
    env = _request(
        "POST",
        "/memory/pin",
        base_url=args.base_url,
        timeout=args.timeout,
        json=body,
    )
    return _print_envelope(env)


def cmd_archive(args: argparse.Namespace) -> int:
    body: dict[str, Any] = {
        "workspace_id": args.workspace,
        "kind": args.kind,
        "id": args.id,
        "agent_id": args.agent_id,
    }
    if args.reason:
        body["reason"] = args.reason
    env = _request(
        "POST",
        "/memory/archive",
        base_url=args.base_url,
        timeout=args.timeout,
        json=body,
    )
    return _print_envelope(env)


def cmd_lint(args: argparse.Namespace) -> int:
    body = {
        "workspace_id": args.workspace,
        "tool_name": args.tool_name,
        "tool_payload": _read_payload(args),
    }
    if args.transcript:
        body["transcript_path"] = args.transcript
    env = _request(
        "POST",
        "/memory/lint",
        base_url=args.base_url,
        timeout=args.timeout,
        json=body,
    )
    return _print_envelope(env)


def cmd_skill(args: argparse.Namespace) -> int:
    env = _request(
        "GET",
        f"/memory/skill/{args.id}",
        base_url=args.base_url,
        timeout=args.timeout,
        params={"workspace_id": args.workspace},
    )
    return _print_envelope(env, text_mode=args.text)


def cmd_rollback(args: argparse.Namespace) -> int:
    body = {
        "workspace_id": args.workspace,
        "kind": args.kind,
        "id": args.id,
        "to_version": args.to_version,
        "why": args.why,
        "agent_id": args.agent_id,
    }
    env = _request(
        "POST",
        "/memory/rollback",
        base_url=args.base_url,
        timeout=args.timeout,
        json=body,
    )
    return _print_envelope(env)


def cmd_versions(args: argparse.Namespace) -> int:
    env = _request(
        "GET",
        "/memory/versions",
        base_url=args.base_url,
        timeout=args.timeout,
        params={"workspace_id": args.workspace, "kind": args.kind, "id": args.id},
    )
    return _print_envelope(env)


# ============================================================
# Argparse wiring
# ============================================================


def _global_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--workspace", default=DEFAULT_WORKSPACE, required=not DEFAULT_WORKSPACE)
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    p.add_argument("--agent-id", default="memory-cli")


def _build_parser() -> argparse.ArgumentParser:  # noqa: PLR0915 — 13 subcommands, flat by design
    parser = argparse.ArgumentParser(prog="memory-cli", description="memory CLI client")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("brief", help="Compose ≤500-token session brief")
    _global_args(p)
    p.add_argument("--task", default=None)
    p.add_argument("--max-tokens", type=int, default=500)
    p.add_argument("--text", action="store_true", help="Plain markdown to stdout")
    p.set_defaults(handler=cmd_brief)

    p = sub.add_parser("list", help="List rows of a kind as compact projections")
    _global_args(p)
    p.add_argument("--kind", required=True)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--pinned-only", action="store_true")
    p.add_argument("--status", default=None)
    p.set_defaults(handler=cmd_list)

    p = sub.add_parser("get", help="Fetch one row by id")
    _global_args(p)
    p.add_argument("--kind", required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--fields", default=None, help="Comma-separated extra columns")
    p.add_argument("--text", action="store_true")
    p.set_defaults(handler=cmd_get)

    p = sub.add_parser("count", help="COUNT(*) for a kind")
    _global_args(p)
    p.add_argument("--kind", required=True)
    p.add_argument("--pinned-only", action="store_true")
    p.add_argument("--status", default=None)
    p.set_defaults(handler=cmd_count)

    p = sub.add_parser("search", help="Multi-kind compact-projection search")
    _global_args(p)
    p.add_argument("query")
    p.add_argument("--kinds", default=None, help="CSV of kinds to scope")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(handler=cmd_search)

    p = sub.add_parser("write", help="Create/update a row")
    _global_args(p)
    p.add_argument("--kind", required=True)
    p.add_argument("--payload", default=None, help="Inline JSON payload")
    p.add_argument("--stdin", action="store_true", help="Read payload JSON from stdin")
    p.set_defaults(handler=cmd_write)

    p = sub.add_parser("edit", help="Partial field update")
    _global_args(p)
    p.add_argument("--kind", required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--payload", default=None)
    p.add_argument("--stdin", action="store_true")
    p.set_defaults(handler=cmd_edit)

    p = sub.add_parser("pin", help="Flip pinned flag")
    _global_args(p)
    p.add_argument("--kind", required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--unpin", action="store_true")
    p.set_defaults(handler=cmd_pin)

    p = sub.add_parser("archive", help="Soft-delete by kind convention")
    _global_args(p)
    p.add_argument("--kind", required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--reason", default=None)
    p.set_defaults(handler=cmd_archive)

    p = sub.add_parser("lint", help="Pre-task advisory")
    _global_args(p)
    p.add_argument("--tool-name", required=True)
    p.add_argument("--payload", default=None)
    p.add_argument("--stdin", action="store_true")
    p.add_argument("--transcript", default=None)
    p.set_defaults(handler=cmd_lint)

    p = sub.add_parser("skill", help="Invoke a skill (returns body_md)")
    _global_args(p)
    p.add_argument("id")
    p.add_argument("--text", action="store_true")
    p.set_defaults(handler=cmd_skill)

    p = sub.add_parser("rollback", help="Restore historical version as new version")
    _global_args(p)
    p.add_argument("--kind", required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--to-version", type=int, required=True)
    p.add_argument("--why", required=True)
    p.set_defaults(handler=cmd_rollback)

    p = sub.add_parser("versions", help="List version history")
    _global_args(p)
    p.add_argument("--kind", required=True)
    p.add_argument("--id", required=True)
    p.set_defaults(handler=cmd_versions)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    rc: int = args.handler(args)
    return rc


if __name__ == "__main__":
    sys.exit(main())
