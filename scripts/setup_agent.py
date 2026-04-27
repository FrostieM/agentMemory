"""One-shot setup for any AI agent on this machine to use agent-memory-lite.

Run from the project root:

    python scripts/setup_agent.py [--yes] [--no-hook] [--project] [--check-only]

What it does (idempotent — safe to re-run):

1. Verifies the venv has the package + the `[mcp]` extra installed.
2. Detects the runtime stack:
   - Ollama binary + daemon + qwen2.5:7b-instruct model
   - Memory SQLite db + LanceDB store
   - Claude Code, Codex, Cursor configuration directories
3. Bootstraps the database if missing.
4. Sets `OLLAMA_PROBE_SKIP` based on Ollama availability.
5. For every detected agent runtime, writes:
   - An MCP server entry pointing at this venv's `agent_memory_lite.mcp.stdio_server`
   - The agent contract (`docs/AGENT_CONTRACT.md`) into the runtime's "always-loaded"
     instructions file (CLAUDE.md / AGENTS.md / .cursorrules).
6. (Claude Code only) Optionally installs a `UserPromptSubmit` hook that calls
   `scripts/inject_memory_context.py` so memory context is injected before every
   prompt, even if the agent forgets to ask. Skip with `--no-hook`.
7. Emits a per-runtime "generic" snippet to stdout for any agent not detected.
8. Smoke-tests the MCP server (initialize + tools/list) and prints a "verified"
   summary.

Use `--check-only` to skip every write step and just report status.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "docs" / "AGENT_CONTRACT.md"
HOOK_SCRIPT = REPO_ROOT / "scripts" / "inject_memory_context.py"
MARKER_BEGIN = "<!-- agent-memory-lite-contract:begin -->"
MARKER_END = "<!-- agent-memory-lite-contract:end -->"
DEFAULT_MODEL = "qwen2.5:7b-instruct"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
SERVICE_URL = "http://127.0.0.1:8765"

OLLAMA_LOCATIONS: tuple[Path, ...] = (
    Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe",
    Path("C:/Program Files/Ollama/ollama.exe"),
    Path("/usr/local/bin/ollama"),
    Path("/opt/homebrew/bin/ollama"),
)


# ---------- printing ----------

GREEN = "\033[32m" if sys.stdout.isatty() else ""
RED = "\033[31m" if sys.stdout.isatty() else ""
YELLOW = "\033[33m" if sys.stdout.isatty() else ""
DIM = "\033[2m" if sys.stdout.isatty() else ""
RESET = "\033[0m" if sys.stdout.isatty() else ""


def ok(msg: str) -> None:
    print(f"{GREEN}[ok]{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}[!]{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"{RED}[x]{RESET} {msg}")


def info(msg: str) -> None:
    print(f"  {msg}")


def section(title: str) -> None:
    print()
    print(f"{DIM}=== {title} ==={RESET}")


# ---------- diagnostics ----------


@dataclass(slots=True)
class Diagnosis:
    venv_python: Path
    package_installed: bool
    mcp_extra_installed: bool
    ollama_binary: Path | None
    ollama_daemon_up: bool
    ollama_models: list[str] = field(default_factory=list)
    qwen_pulled: bool = False
    db_path: Path | None = None
    db_exists: bool = False
    service_running: bool = False
    runtimes: dict[str, bool] = field(default_factory=dict)


def find_ollama_binary() -> Path | None:
    on_path = shutil.which("ollama")
    if on_path:
        return Path(on_path)
    for candidate in OLLAMA_LOCATIONS:
        if candidate.exists():
            return candidate
    return None


def probe_ollama_daemon() -> tuple[bool, list[str]]:
    try:
        response = httpx.get(f"{DEFAULT_OLLAMA_URL}/api/tags", timeout=4.0)
        response.raise_for_status()
    except httpx.HTTPError:
        return False, []
    data = response.json()
    models = [str(m.get("name", "")) for m in data.get("models", [])]
    return True, models


def probe_service() -> bool:
    try:
        response = httpx.get(f"{SERVICE_URL}/health", timeout=2.0)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def diagnose() -> Diagnosis:
    venv_python = Path(sys.executable)
    try:
        import agent_memory_lite  # noqa: F401, PLC0415

        package_installed = True
    except ImportError:
        package_installed = False
    try:
        import mcp  # noqa: F401, PLC0415

        mcp_extra = True
    except ImportError:
        mcp_extra = False

    binary = find_ollama_binary()
    daemon_up, models = (False, [])
    if binary is not None:
        daemon_up, models = probe_ollama_daemon()

    db_path = REPO_ROOT / ".agent_memory" / "memory.db"
    return Diagnosis(
        venv_python=venv_python,
        package_installed=package_installed,
        mcp_extra_installed=mcp_extra,
        ollama_binary=binary,
        ollama_daemon_up=daemon_up,
        ollama_models=models,
        qwen_pulled=any(DEFAULT_MODEL in m for m in models),
        db_path=db_path,
        db_exists=db_path.exists(),
        service_running=probe_service(),
        runtimes={
            "claude-code": (Path.home() / ".claude").exists(),
            "codex": (Path.home() / ".codex").exists(),
            "cursor": (Path.home() / ".cursor").exists(),
        },
    )


def print_diagnosis(diag: Diagnosis) -> None:
    section("Environment")
    info(f"venv python:           {diag.venv_python}")
    (ok if diag.package_installed else fail)(
        "agent-memory-lite package installed"
        if diag.package_installed
        else 'agent-memory-lite package NOT installed; run `pip install -e ".[dev,mcp]"`'
    )
    (ok if diag.mcp_extra_installed else warn)(
        "mcp SDK installed"
        if diag.mcp_extra_installed
        else 'mcp SDK NOT installed; run `pip install -e ".[mcp]"` for stdio server'
    )

    section("Ollama")
    if diag.ollama_binary is None:
        warn("Ollama binary not found. LLM extraction will be no-op.")
        info("Install: https://ollama.com/download")
    else:
        ok(f"Ollama binary at {diag.ollama_binary}")
        if diag.ollama_daemon_up:
            ok(f"Daemon reachable at {DEFAULT_OLLAMA_URL}")
            if diag.qwen_pulled:
                ok(f"Model {DEFAULT_MODEL} pulled")
            else:
                warn(f"Model {DEFAULT_MODEL} not pulled. Run `ollama pull {DEFAULT_MODEL}`.")
        else:
            warn("Ollama daemon not running. Start the Ollama app or run `ollama serve`.")

    section("Memory database")
    info(f"db path:               {diag.db_path}")
    if diag.db_exists:
        ok("memory.db exists")
    else:
        warn("memory.db missing — will bootstrap")
    info(f"http service running:  {diag.service_running}")

    section("Detected agent runtimes")
    for name, present in diag.runtimes.items():
        (ok if present else info)(f"{name}: {'present' if present else 'not found'}")


# ---------- bootstrap + .env ----------


def bootstrap_db() -> None:
    subprocess.run(
        [str(Path(sys.executable)), str(REPO_ROOT / "scripts" / "bootstrap_db.py")],
        check=True,
        cwd=str(REPO_ROOT),
    )


def write_env(diag: Diagnosis) -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        shutil.copy(REPO_ROOT / ".env.example", env_path)
        ok(f"created {env_path}")

    text = env_path.read_text(encoding="utf-8")
    desired_skip = "true" if not (diag.ollama_daemon_up and diag.qwen_pulled) else "false"
    new_text = []
    flipped = False
    for line in text.splitlines():
        if line.startswith("OLLAMA_PROBE_SKIP="):
            if line != f"OLLAMA_PROBE_SKIP={desired_skip}":
                new_text.append(f"OLLAMA_PROBE_SKIP={desired_skip}")
                flipped = True
            else:
                new_text.append(line)
        else:
            new_text.append(line)
    if flipped:
        env_path.write_text("\n".join(new_text) + "\n", encoding="utf-8")
        ok(f"set OLLAMA_PROBE_SKIP={desired_skip} in .env")


# ---------- contract markdown ----------


def render_contract_block() -> str:
    body = CONTRACT_PATH.read_text(encoding="utf-8")
    return f"\n{MARKER_BEGIN}\n\n{body}\n\n{MARKER_END}\n"


def upsert_contract(path: Path) -> str:
    """Insert or replace the contract block in `path`. Returns 'created' / 'updated' / 'unchanged'."""
    block = render_contract_block()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(block.lstrip("\n"), encoding="utf-8")
        return "created"
    existing = path.read_text(encoding="utf-8")
    begin = existing.find(MARKER_BEGIN)
    if begin == -1:
        path.write_text(existing.rstrip() + "\n" + block, encoding="utf-8")
        return "updated"
    end = existing.find(MARKER_END, begin)
    if end == -1:
        new = existing[:begin].rstrip() + block
    else:
        new = existing[:begin].rstrip() + block + existing[end + len(MARKER_END) :].lstrip("\n")
    if new == existing:
        return "unchanged"
    path.write_text(new, encoding="utf-8")
    return "updated"


# ---------- runtime configurators ----------


def claude_mcp_entry(
    python_exe: Path,
    *,
    project_root: Path | None = None,
    workspace_id: str | None = None,
) -> dict[str, object]:
    # Project isolation comes primarily from the physical path (MEMORY_DB_PATH +
    # VECTOR_DB_PATH). workspace_id is the logical namespace inside that database.
    env: dict[str, str] = {"OLLAMA_PROBE_SKIP": os.environ.get("OLLAMA_PROBE_SKIP", "false")}
    if workspace_id:
        env["MEMORY_WORKSPACE_ID"] = workspace_id
    if project_root is not None:
        env["MEMORY_DB_PATH"] = str(project_root / ".agent_memory" / "memory.db")
        env["VECTOR_DB_PATH"] = str(project_root / ".agent_memory" / "vectors.lance")
    return {
        "command": str(python_exe),
        "args": ["-m", "agent_memory_lite.mcp.stdio_server"],
        "env": env,
    }


def configure_claude_code(diag: Diagnosis, *, install_hook: bool) -> None:
    section("Claude Code")
    settings_path = Path.home() / ".claude" / "settings.json"
    settings: dict[str, object] = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            warn(f"{settings_path} is not valid JSON — backing up to .bak")
            settings_path.with_suffix(".json.bak").write_text(
                settings_path.read_text(encoding="utf-8"), encoding="utf-8"
            )
            settings = {}
    if not isinstance(settings, dict):
        settings = {}

    mcp_servers = settings.setdefault("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}
        settings["mcpServers"] = mcp_servers
    mcp_servers["agent-memory-lite"] = claude_mcp_entry(diag.venv_python)
    ok("MCP server entry written to ~/.claude/settings.json")

    if install_hook:
        hooks = settings.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            hooks = {}
            settings["hooks"] = hooks
        ups = hooks.setdefault("UserPromptSubmit", [])
        if not isinstance(ups, list):
            ups = []
            hooks["UserPromptSubmit"] = ups

        hook_command = f'"{diag.venv_python}" "{HOOK_SCRIPT}"'
        marker = "agent-memory-lite-inject"
        existing = next(
            (
                entry
                for entry in ups
                if isinstance(entry, dict)
                and isinstance(entry.get("hooks"), list)
                and any(
                    isinstance(h, dict) and marker in str(h.get("command", ""))
                    for h in entry["hooks"]
                )
            ),
            None,
        )
        new_hook = {
            "type": "command",
            "command": hook_command + f" # {marker}",
        }
        if existing is None:
            ups.append({"hooks": [new_hook]})
        else:
            existing["hooks"] = [new_hook]
        ok("UserPromptSubmit hook installed (auto-injects memory context per prompt)")
    else:
        warn("hook install skipped (--no-hook); agent only sees memory if it asks")

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

    contract = Path.home() / ".claude" / "CLAUDE.md"
    status = upsert_contract(contract)
    ok(f"contract {status} in {contract}")


def configure_codex(diag: Diagnosis) -> None:
    section("Codex")
    config_path = Path.home() / ".codex" / "config.toml"
    block = dedent(
        f"""
        # agent-memory-lite >>> (managed by setup_agent.py)
        [mcp_servers.agent-memory-lite]
        command = {json.dumps(str(diag.venv_python))}
        args = ["-m", "agent_memory_lite.mcp.stdio_server"]

        [mcp_servers.agent-memory-lite.env]
        OLLAMA_PROBE_SKIP = {json.dumps(os.environ.get("OLLAMA_PROBE_SKIP", "false"))}
        # <<< agent-memory-lite
        """
    ).strip()

    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        original = config_path.read_text(encoding="utf-8")
        marker_b = "# agent-memory-lite >>>"
        marker_e = "# <<< agent-memory-lite"
        if marker_b in original:
            head, _, tail = original.partition(marker_b)
            _, _, tail = tail.partition(marker_e)
            new = head.rstrip() + "\n\n" + block + "\n" + tail.lstrip("\n")
        else:
            new = original.rstrip() + "\n\n" + block + "\n"
    else:
        new = block + "\n"
    config_path.write_text(new, encoding="utf-8")
    ok(f"MCP server entry written to {config_path}")

    contract = Path.home() / ".codex" / "AGENTS.md"
    status = upsert_contract(contract)
    ok(f"contract {status} in {contract}")


def configure_cursor(diag: Diagnosis) -> None:
    section("Cursor")
    mcp_path = Path.home() / ".cursor" / "mcp.json"
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, object] = {}
    if mcp_path.exists():
        try:
            existing = json.loads(mcp_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    servers = existing.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = {}
        existing["mcpServers"] = servers
    servers["agent-memory-lite"] = claude_mcp_entry(diag.venv_python)
    mcp_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    ok(f"MCP server entry written to {mcp_path}")

    contract = Path.home() / ".cursor" / "rules" / "agent-memory-lite.md"
    status = upsert_contract(contract)
    ok(f"contract {status} in {contract}")


def configure_project(  # noqa: PLR0915
    diag: Diagnosis,
    project_root: Path,
    *,
    workspace_id: str,
) -> None:
    section(f"Project mode: {project_root}")
    if project_root == REPO_ROOT:
        warn(
            "running --project from inside the agent-memory-lite repo. "
            "This will create a project-scoped memory at "
            f"{project_root / '.agent_memory'}, separate from any global one."
        )

    settings_path = project_root / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings: dict[str, object] = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            warn(f"{settings_path} is not valid JSON — backing up to .bak")
            settings_path.with_suffix(".json.bak").write_text(
                settings_path.read_text(encoding="utf-8"), encoding="utf-8"
            )
            settings = {}
    if not isinstance(settings, dict):
        settings = {}

    servers = settings.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = {}
        settings["mcpServers"] = servers
    servers["agent-memory-lite"] = claude_mcp_entry(
        diag.venv_python,
        project_root=project_root,
        workspace_id=workspace_id,
    )

    db_path = project_root / ".agent_memory" / "memory.db"
    vector_path = project_root / ".agent_memory" / "vectors.lance"
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks
    ups = hooks.setdefault("UserPromptSubmit", [])
    if not isinstance(ups, list):
        ups = []
        hooks["UserPromptSubmit"] = ups
    marker = "agent-memory-lite-inject"
    hook_command = (
        f'"{diag.venv_python}" "{HOOK_SCRIPT}" --db-path "{db_path}" '
        f'--vector-path "{vector_path}" --workspace "{workspace_id}"'
    )
    new_hook = {"type": "command", "command": hook_command + f" # {marker}"}
    existing = next(
        (
            entry
            for entry in ups
            if isinstance(entry, dict)
            and isinstance(entry.get("hooks"), list)
            and any(
                isinstance(h, dict) and marker in str(h.get("command", "")) for h in entry["hooks"]
            )
        ),
        None,
    )
    if existing is None:
        ups.append({"hooks": [new_hook]})
    else:
        existing["hooks"] = [new_hook]
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    ok(f"MCP entry + project-scoped hook written to {settings_path}")

    contract_path = project_root / "CLAUDE.md"
    status = upsert_contract(contract_path)
    ok(f"contract {status} in {contract_path}")

    agents_path = project_root / "AGENTS.md"
    status_agents = upsert_contract(agents_path)
    ok(f"contract {status_agents} in {agents_path} (Codex / generic agent fallback)")

    db_path = project_root / ".agent_memory" / "memory.db"
    if db_path.exists():
        ok(f"project memory db already at {db_path}")
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["MEMORY_DB_PATH"] = str(db_path)
        env["VECTOR_DB_PATH"] = str(project_root / ".agent_memory" / "vectors.lance")
        env["MEMORY_WORKSPACE_ID"] = workspace_id
        subprocess.run(
            [str(diag.venv_python), str(REPO_ROOT / "scripts" / "bootstrap_db.py")],
            check=True,
            cwd=str(project_root),
            env=env,
        )
        ok(f"bootstrapped {db_path}")


def emit_generic_snippets(diag: Diagnosis) -> None:
    section("Generic snippets (paste anywhere else)")
    print(
        "MCP server (any MCP-aware client) — JSON form:\n"
        + json.dumps(
            {"mcpServers": {"agent-memory-lite": claude_mcp_entry(diag.venv_python)}},
            indent=2,
        )
    )
    print(
        "\nSystem-prompt-only fallback: paste the contents of\n"
        f"  {CONTRACT_PATH}\n"
        "into your agent's system / developer message at session start.\n"
    )


# ---------- smoke test ----------


def smoke_test_mcp(diag: Diagnosis) -> bool:
    section("Smoke test: MCP stdio server")
    if not diag.mcp_extra_installed:
        warn("mcp SDK not installed; skipping smoke test")
        return False
    payload = (
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":'
        '"2024-11-05","capabilities":{},"clientInfo":{"name":"setup","version":"0"}}}\n'
        '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
    )
    env = dict(os.environ)
    env.setdefault("OLLAMA_PROBE_SKIP", "true")
    try:
        proc = subprocess.run(
            [str(diag.venv_python), "-m", "agent_memory_lite.mcp.stdio_server"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(REPO_ROOT),
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        fail("MCP server did not respond within 30s")
        return False

    if "agent-memory-lite" not in proc.stdout or '"memory_get_context"' not in proc.stdout:
        fail("MCP server response missing expected fields")
        info(f"stderr tail: {proc.stderr[-500:]}")
        return False
    ok("initialize + tools/list returned the memory tool registry")
    return True


# ---------- main ----------


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure AI agents on this machine.")
    parser.add_argument("--check-only", action="store_true", help="Diagnose, do not write.")
    parser.add_argument("--no-hook", action="store_true", help="Skip Claude Code hook install.")
    parser.add_argument("--yes", action="store_true", help="Non-interactive (assume yes).")
    parser.add_argument(
        "--workspace",
        default="default",
        help="Logical workspace_id to use inside the selected memory database.",
    )
    parser.add_argument(
        "--project",
        nargs="?",
        const=".",
        default=None,
        metavar="PATH",
        help="Configure per-project memory (default: current directory). "
        "Writes <project>/.claude/settings.json + <project>/CLAUDE.md + "
        "<project>/AGENTS.md, and bootstraps <project>/.agent_memory/. "
        "Each project gets its own MEMORY_DB_PATH so memories stay isolated.",
    )
    args = parser.parse_args()

    diag = diagnose()
    print_diagnosis(diag)

    if args.check_only:
        section("--check-only: no writes performed")
        return 0

    if not diag.package_installed or not diag.mcp_extra_installed:
        fail("Install missing pieces first, then re-run.")
        info('  pip install -e ".[dev,mcp]"')
        return 1

    write_env(diag)

    if args.project is not None:
        project_root = Path(args.project).resolve()
        configure_project(diag, project_root, workspace_id=args.workspace)
        section("Done (project mode)")
        print(
            f"This project ({project_root.name}) now has its own memory at\n"
            f"  {project_root / '.agent_memory'}\n"
            f"using workspace_id={args.workspace!r}, and its own CLAUDE.md / AGENTS.md contract.\n"
            "Restart your agent runtime and it will see ONLY this project's memory.\n"
        )
        smoke_test_mcp(diag)
        return 0

    section("Bootstrap database")
    if not diag.db_exists:
        bootstrap_db()
        ok("database created")
    else:
        ok("database already present")

    if diag.runtimes["claude-code"]:
        configure_claude_code(diag, install_hook=not args.no_hook)
    if diag.runtimes["codex"]:
        configure_codex(diag)
    if diag.runtimes["cursor"]:
        configure_cursor(diag)
    if not any(diag.runtimes.values()):
        warn("No known agent runtime detected — emitting generic snippets only.")
    emit_generic_snippets(diag)

    smoke_test_mcp(diag)

    section("Done")
    print(
        "Restart your agent runtime (Claude Code, Codex, Cursor) for the new MCP\n"
        "config to take effect. The contract lives in each runtime's always-loaded\n"
        "instructions file.\n"
    )
    if not diag.qwen_pulled:
        info(
            f"Heads up: {DEFAULT_MODEL} not pulled. LLM extraction will be a no-op\n"
            f"  until you run `ollama pull {DEFAULT_MODEL}` and restart the service.\n"
        )
    if not diag.service_running:
        info(
            "Heads up: HTTP service not running. The MCP stdio server doesn't need\n"
            "it, but the UserPromptSubmit hook does. Start it in another terminal:\n"
            "  python -m agent_memory_lite\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
