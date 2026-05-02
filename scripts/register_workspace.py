"""Register, list, or remove workspaces in the hub registry.

Talks directly to the JSON file (`~/.agent_memory/workspaces.json` by
default). No HTTP service required, so this works whether the service is up,
down, or being moved between hub and strict mode.

Examples:

    # Register the agent-memory-lite project's own workspace.
    python scripts/register_workspace.py register \\
        --workspace agentLight \\
        --project /path/to/agent-memory-lite

    # Register copyBot.
    python scripts/register_workspace.py register \\
        --workspace copyBot \\
        --project /path/to/copyBot

    # See current registry.
    python scripts/register_workspace.py list

    # Drop an entry (does not delete the underlying DB).
    python scripts/register_workspace.py remove --workspace agentLight
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_memory_lite.config.workspace_registry import (  # noqa: E402
    WorkspaceRegistry,
)


def default_registry_path() -> Path:
    override = os.environ.get("MEMORY_WORKSPACES_FILE")
    if override:
        return Path(override)
    return Path.home() / ".agent_memory" / "workspaces.json"


def cmd_register(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve() if args.project else None
    if project is None:
        sys.stderr.write("--project is required for register\n")
        return 2
    if not project.exists():
        sys.stderr.write(f"project root does not exist: {project}\n")
        return 2
    db_path = Path(args.db_path) if args.db_path else project / ".agent_memory" / "memory.db"
    vec_path = (
        Path(args.vector_path) if args.vector_path else project / ".agent_memory" / "vectors.lance"
    )
    registry = WorkspaceRegistry(default_registry_path())
    entry = registry.register(
        workspace_id=args.workspace,
        db_path=str(db_path),
        vector_path=str(vec_path),
        label=args.label or project.name,
        project_root=str(project),
    )
    print(json.dumps(entry.to_dict(), indent=2))
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    registry = WorkspaceRegistry(default_registry_path())
    entries = [entry.to_dict() for entry in registry.list()]
    print(json.dumps({"registry_path": str(registry.path), "workspaces": entries}, indent=2))
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    registry = WorkspaceRegistry(default_registry_path())
    removed = registry.remove(args.workspace)
    print(json.dumps({"workspace_id": args.workspace, "removed": removed}))
    return 0 if removed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    reg = sub.add_parser("register", help="Add or update a workspace entry.")
    reg.add_argument("--workspace", required=True, help="workspace_id, e.g. copyBot")
    reg.add_argument("--project", help="Project root. Defaults to <project>/.agent_memory/...")
    reg.add_argument(
        "--db-path", help="Override DB path (default <project>/.agent_memory/memory.db)"
    )
    reg.add_argument(
        "--vector-path", help="Override vector path (default <project>/.agent_memory/vectors.lance)"
    )
    reg.add_argument("--label", help="Friendly label, defaults to project folder name")
    reg.set_defaults(func=cmd_register)

    lst = sub.add_parser("list", help="Print current registry.")
    lst.set_defaults(func=cmd_list)

    rem = sub.add_parser("remove", help="Drop an entry. Does not touch SQLite/Lance.")
    rem.add_argument("--workspace", required=True)
    rem.set_defaults(func=cmd_remove)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
