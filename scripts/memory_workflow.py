"""Agent workflow wrapper for preflight context and completion recording."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8765"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run memory-aware agent workflow steps.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--workspace", "--workspace-id", dest="workspace", required=True)
    parser.add_argument("--api-token-file", default=None)
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    preflight = sub.add_parser("preflight", help="Fetch context before a task.")
    preflight.add_argument("--query", required=True)
    preflight.add_argument("--task-id", default=None)
    preflight.add_argument("--max-tokens", type=int, default=2500)
    preflight.add_argument("--historical", action="store_true")
    preflight.add_argument("--file", dest="files", action="append", default=[])
    preflight.add_argument("--dry-run", action="store_true")
    preflight.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    complete = sub.add_parser("complete", help="Record task completion.")
    complete.add_argument("--task-id", required=True)
    complete.add_argument("--goal", required=True)
    complete.add_argument("--raw-text", required=True)
    complete.add_argument("--status", default="done")
    complete.add_argument("--next-action", default="")
    complete.add_argument("--importance", type=float, default=0.7)
    complete.add_argument("--dry-run", action="store_true")
    complete.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    return parser


def _headers(token_file: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token_file:
        token = Path(token_file).read_text(encoding="utf-8").strip()
        if not token:
            raise ValueError(f"API token file is empty: {token_file}")
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _post_json(
    client: httpx.Client,
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
) -> dict[str, Any]:
    response = client.post(url, json=payload, headers=headers)
    response.raise_for_status()
    parsed = response.json()
    return parsed if isinstance(parsed, dict) else {"response": parsed}


def _preflight_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "workspace_id": args.workspace,
        "task_id": args.task_id,
        "query": args.query,
        "files_in_scope": args.files,
        "max_tokens": args.max_tokens,
        "historical": args.historical,
    }


def _completion_payloads(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    return {
        "ingest_episode": {
            "workspace_id": args.workspace,
            "task_id": args.task_id,
            "source_type": "agent_action",
            "raw_text": args.raw_text,
            "trust_level": "agent_observed",
            "importance": args.importance,
        },
        "update_task_state": {
            "workspace_id": args.workspace,
            "task_id": args.task_id,
            "goal": args.goal,
            "status": args.status,
            "current_plan": [],
            "completed_steps": [args.raw_text],
            "next_action": args.next_action or None,
            "blockers": [],
            "files_in_scope": [],
        },
    }


def _print(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"status={payload['status']} command={payload['command']}")
    if payload.get("context_text"):
        print(payload["context_text"])
    if payload.get("episode_id"):
        print(f"episode_id={payload['episode_id']}")
    if payload.get("state_id"):
        print(f"state_id={payload['state_id']}")


def _run_preflight(args: argparse.Namespace, headers: dict[str, str]) -> dict[str, Any]:
    payload = _preflight_payload(args)
    if args.dry_run:
        return {"status": "ok", "command": "preflight", "dry_run": True, "request": payload}
    with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
        health = client.get("/health")
        health.raise_for_status()
        context = _post_json(client, "/memory/get_context", payload, headers=headers)
    return {
        "status": "ok",
        "command": "preflight",
        "health": health.json(),
        "context_text": context.get("context_text", ""),
        "sources": context.get("sources", []),
    }


def _run_complete(args: argparse.Namespace, headers: dict[str, str]) -> dict[str, Any]:
    payloads = _completion_payloads(args)
    if args.dry_run:
        return {"status": "ok", "command": "complete", "dry_run": True, "requests": payloads}
    with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
        episode = _post_json(
            client,
            "/memory/ingest_episode",
            payloads["ingest_episode"],
            headers=headers,
        )
        state = _post_json(
            client,
            "/memory/update_task_state",
            payloads["update_task_state"],
            headers=headers,
        )
    return {
        "status": "ok",
        "command": "complete",
        "episode_id": episode.get("episode_id"),
        "chunk_id": episode.get("chunk_id"),
        "candidates_written": episode.get("candidates_written"),
        "state_id": state.get("state_id"),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        headers = _headers(args.api_token_file)
        if args.command == "preflight":
            payload = _run_preflight(args, headers)
        elif args.command == "complete":
            payload = _run_complete(args, headers)
        else:  # pragma: no cover - argparse enforces subcommands.
            raise ValueError(f"unsupported command {args.command!r}")
        _print(payload, as_json=args.json)
    except Exception as exc:
        print(f"memory_workflow_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
