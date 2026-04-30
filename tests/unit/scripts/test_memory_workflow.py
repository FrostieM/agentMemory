from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import ModuleType


def _load_script(name: str) -> ModuleType:
    path = Path(__file__).parents[3] / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_workflow_preflight_payload() -> None:
    script = _load_script("memory_workflow.py")
    args = argparse.Namespace(
        workspace="project",
        task_id="task-1",
        query="fix retrieval",
        files=["src/foo.py"],
        max_tokens=1200,
        historical=True,
    )

    payload = script._preflight_payload(args)

    assert payload == {
        "workspace_id": "project",
        "task_id": "task-1",
        "query": "fix retrieval",
        "files_in_scope": ["src/foo.py"],
        "max_tokens": 1200,
        "historical": True,
    }


def test_workflow_completion_payloads_do_not_hide_task_state() -> None:
    script = _load_script("memory_workflow.py")
    args = argparse.Namespace(
        workspace="project",
        task_id="task-1",
        goal="Finish task",
        raw_text="Implemented and verified change.",
        status="done",
        next_action="Commit",
        importance=0.8,
    )

    payloads = script._completion_payloads(args)

    assert payloads["ingest_episode"]["workspace_id"] == "project"
    assert payloads["ingest_episode"]["raw_text"] == "Implemented and verified change."
    assert payloads["update_task_state"]["status"] == "done"
    assert payloads["update_task_state"]["next_action"] == "Commit"


def test_workflow_headers_read_bearer_token(tmp_path: Path) -> None:
    script = _load_script("memory_workflow.py")
    token_file = tmp_path / "token"
    token_file.write_text("secret\n", encoding="utf-8")

    headers = script._headers(str(token_file))

    assert headers["Authorization"] == "Bearer secret"
    assert headers["Content-Type"] == "application/json"


def test_workflow_accepts_json_after_subcommand() -> None:
    script = _load_script("memory_workflow.py")

    args = script._parser().parse_args(
        [
            "--workspace",
            "project",
            "preflight",
            "--query",
            "task",
            "--dry-run",
            "--json",
        ]
    )

    assert args.json is True
    assert args.command == "preflight"
