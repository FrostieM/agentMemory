"""Agent workflow wrapper for v3 brief/search preflight and completion writes."""

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
    preflight.add_argument("--capability-query", default=None)
    preflight.add_argument("--role", action="append", default=[])
    preflight.add_argument("--skill", action="append", default=[])
    preflight.add_argument("--playbook", action="append", default=[])
    preflight.add_argument("--dry-run", action="store_true")
    preflight.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    complete = sub.add_parser("complete", help="Record task completion.")
    complete.add_argument("--task-id", required=True)
    complete.add_argument("--goal", required=True)
    complete.add_argument("--raw-text", required=True)
    complete.add_argument("--status", default="done")
    complete.add_argument("--next-action", default="")
    complete.add_argument("--importance", type=float, default=0.7)
    complete.add_argument("--role", action="append", default=[])
    complete.add_argument("--skill", action="append", default=[])
    complete.add_argument("--playbook", action="append", default=[])
    complete.add_argument("--verification", action="append", default=[])
    complete.add_argument("--decision-id", action="append", default=[])
    complete.add_argument("--theory-id", action="append", default=[])
    complete.add_argument("--experiment-id", action="append", default=[])
    complete.add_argument("--insight-id", action="append", default=[])
    complete.add_argument("--strict", action="store_true")
    complete.add_argument("--allow-episode-only", action="store_true")
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


def _get_json(
    client: httpx.Client,
    url: str,
    params: dict[str, Any],
    *,
    headers: dict[str, str],
) -> dict[str, Any]:
    response = client.get(url, params=params, headers=headers)
    response.raise_for_status()
    parsed = response.json()
    return parsed if isinstance(parsed, dict) else {"response": parsed}


def _unwrap_data(envelope: dict[str, Any]) -> Any:
    return envelope.get("data") if isinstance(envelope.get("data"), (dict, list)) else envelope


def _preflight_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "workspace_id": args.workspace,
        "task_id": args.task_id,
        "query": args.query,
        "files_in_scope": args.files,
        "max_tokens": args.max_tokens,
        "historical": args.historical,
    }


def _brief_params(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "workspace_id": args.workspace,
        "task": args.query,
        "max_tokens": args.max_tokens,
    }


def _search_payload(
    args: argparse.Namespace,
    *,
    query: str | None = None,
    kinds: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workspace_id": args.workspace,
        "query": query or args.query,
        "limit": 8,
    }
    if kinds:
        payload["kinds"] = kinds
    return payload


def _role_trace(args: argparse.Namespace) -> dict[str, list[str]]:
    return {
        "roles": list(dict.fromkeys(args.role)),
        "skills": list(dict.fromkeys(args.skill)),
        "playbooks": list(dict.fromkeys(args.playbook)),
    }


def _linked_memory(args: argparse.Namespace) -> dict[str, list[str]]:
    return {
        "decisions": list(dict.fromkeys(args.decision_id)),
        "theories": list(dict.fromkeys(args.theory_id)),
        "experiments": list(dict.fromkeys(args.experiment_id)),
        "insights": list(dict.fromkeys(args.insight_id)),
    }


def _nonempty_link_count(items: dict[str, list[str]]) -> int:
    return sum(len(values) for values in items.values())


def _format_completion_text(args: argparse.Namespace) -> str:
    lines = [args.raw_text.strip()]
    trace = _role_trace(args)
    linked = _linked_memory(args)
    if _nonempty_link_count(trace):
        lines.append("")
        lines.append("Role activation trace:")
        for label, values in trace.items():
            if values:
                lines.append(f"- {label}: {', '.join(values)}")
    if args.verification:
        lines.append("")
        lines.append("Verification evidence:")
        for item in args.verification:
            lines.append(f"- {item}")
    if _nonempty_link_count(linked):
        lines.append("")
        lines.append("Linked memory objects:")
        for label, values in linked.items():
            if values:
                lines.append(f"- {label}: {', '.join(values)}")
    return "\n".join(lines)


def _validate_completion(args: argparse.Namespace) -> None:
    if not args.strict:
        return
    if not args.raw_text.strip():
        raise ValueError("--raw-text is required in strict mode")
    if not args.verification:
        raise ValueError("--verification is required in strict mode")
    if _nonempty_link_count(_role_trace(args)) == 0:
        raise ValueError("--role, --skill, or --playbook is required in strict mode")
    if _nonempty_link_count(_linked_memory(args)) == 0 and not args.allow_episode_only:
        raise ValueError(
            "strict mode requires --decision-id/--theory-id/--experiment-id/--insight-id "
            "or explicit --allow-episode-only"
        )


def _completion_payloads(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    _validate_completion(args)
    raw_text = _format_completion_text(args)
    return {
        "write_episode": {
            "workspace_id": args.workspace,
            "kind": "episode",
            "payload": {
                "task_id": args.task_id,
                "source_type": "agent_action",
                "raw_text": raw_text,
                "trust_level": "agent_observed",
                "importance": args.importance,
            },
        },
        "write_task": {
            "workspace_id": args.workspace,
            "kind": "task",
            "payload": {
                "task_id": args.task_id,
                "goal": args.goal,
                "status": args.status,
                "current_plan": [],
                "completed_steps": [raw_text],
                "next_action": args.next_action or None,
                "blockers": [],
                "files_in_scope": [],
            },
        },
    }


def _print(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"status={payload['status']} command={payload['command']}")
    if payload.get("body_md"):
        print(payload["body_md"])
    if payload.get("episode_id"):
        print(f"episode_id={payload['episode_id']}")
    if payload.get("state_id"):
        print(f"state_id={payload['state_id']}")


def _run_preflight(args: argparse.Namespace, headers: dict[str, str]) -> dict[str, Any]:
    brief_params = _brief_params(args)
    search_payload = _search_payload(args)
    capability_payload = _search_payload(
        args,
        query=args.capability_query or args.query,
        kinds=["skill"],
    )
    if args.dry_run:
        return {
            "status": "ok",
            "command": "preflight",
            "dry_run": True,
            "brief_request": brief_params,
            "search_request": search_payload,
            "capability_request": capability_payload,
            "role_activation_trace": _role_trace(args),
        }
    with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
        health = client.get("/health")
        health.raise_for_status()
        brief = _get_json(client, "/memory/brief", brief_params, headers=headers)
        search_results = _post_json(client, "/memory/search", search_payload, headers=headers)
        capabilities = _post_json(
            client,
            "/memory/search",
            capability_payload,
            headers=headers,
        )
    brief_data = _unwrap_data(brief)
    return {
        "status": "ok",
        "command": "preflight",
        "health": health.json(),
        "body_md": brief_data.get("body_md", "") if isinstance(brief_data, dict) else "",
        "sources": _unwrap_data(search_results),
        "role_activation_trace": _role_trace(args),
        "capability_candidates": _unwrap_data(capabilities),
    }


def _run_complete(args: argparse.Namespace, headers: dict[str, str]) -> dict[str, Any]:
    payloads = _completion_payloads(args)
    if args.dry_run:
        return {
            "status": "ok",
            "command": "complete",
            "dry_run": True,
            "requests": payloads,
        }
    with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
        episode = _post_json(
            client,
            "/memory/write",
            payloads["write_episode"],
            headers=headers,
        )
        state = _post_json(
            client,
            "/memory/write",
            payloads["write_task"],
            headers=headers,
        )
        episode_data = _unwrap_data(episode)
        episode_id = (
            episode_data.get("id") or episode_data.get("episode_id")
            if isinstance(episode_data, dict)
            else None
        )
    state_data = _unwrap_data(state)
    return {
        "status": "ok",
        "command": "complete",
        "strict": args.strict,
        "role_activation_trace": _role_trace(args),
        "verification": list(args.verification),
        "linked_memory": _linked_memory(args),
        "episode_id": episode_id,
        "chunk_id": episode_data.get("chunk_id") if isinstance(episode_data, dict) else None,
        "candidates_written": (
            episode_data.get("candidates_written") if isinstance(episode_data, dict) else None
        ),
        "state_id": (
            state_data.get("id") or state_data.get("state_id")
            if isinstance(state_data, dict)
            else None
        ),
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
