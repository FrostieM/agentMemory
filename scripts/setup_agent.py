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
6. (Claude Code only) Optionally installs the v3 discipline hooks:
   - `UserPromptSubmit` → `scripts/inject_memory_brief_v3.py` (≤500-token
     compact brief composed from v3 projections).
   - `PostToolUse` (Edit|Write|NotebookEdit|MultiEdit) →
     `scripts/post_edit_enqueue.py` (digest worker queue feeder).
   - `PreToolUse` enforcement (unchanged from v2).
   The legacy v2 `inject_memory_context.py` hook is auto-evicted on every
   run — v3 is the canonical surface. Skip all hook wiring with `--no-hook`.
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
from typing import Any

import httpx

from agent_memory_lite.bootstrap.claude_pre_tool_use_hook import (
    install_pre_tool_use_hook,
)
from agent_memory_lite.bootstrap.project_pre_commit_hook import (
    install_project_pre_commit_hook,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "docs" / "AGENT_CONTRACT.md"
# v3.0.0 discipline stack hooks (see docs/V3_AGENT_RUNTIMES.md).
# The legacy v2 ``scripts/inject_memory_context.py`` hook still ships as
# a backwards-compat surface but is no longer installed by this script;
# ``_remove_v2_inject_hook`` evicts it from settings.json on every run.
V3_BRIEF_HOOK_SCRIPT = REPO_ROOT / "scripts" / "inject_memory_brief_v3.py"
V3_POSTEDIT_HOOK_SCRIPT = REPO_ROOT / "scripts" / "post_edit_enqueue.py"
V3_SCHEMA_PATH = REPO_ROOT / "migrations" / "v3" / "0001_init.sql"
V3_POSTTOOLUSE_MATCHER = "Edit|Write|NotebookEdit|MultiEdit"
PRETOOLUSE_HOOK_SCRIPT = REPO_ROOT / "scripts" / "pre_tool_use_check.py"
PRETOOLUSE_HOOK_MATCHER = "Edit|Write|NotebookEdit|Bash|mcp__agent-memory-lite__memory_.*"
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


def _remove_v2_inject_hook(settings: dict[str, object]) -> int:
    """Strip the legacy ``agent-memory-lite-inject`` entry from UserPromptSubmit.

    v2's ``inject_memory_context.py`` hook used to ship alongside the v3
    brief hook; running both on every prompt doubles token cost.  v3 is
    now the canonical surface, so future setup_agent runs evict the v2
    entry.  Idempotent: returns the count actually removed (0 when the
    hook was never installed or already evicted).
    """
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    ups = hooks.get("UserPromptSubmit")
    if not isinstance(ups, list):
        return 0
    removed = 0
    kept: list[dict[str, object]] = []
    for entry in ups:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        inner = entry.get("hooks", [])
        if not isinstance(inner, list):
            kept.append(entry)
            continue
        survivors = [
            h
            for h in inner
            if not (
                isinstance(h, dict)
                and "agent-memory-lite-inject" in str(h.get("command", ""))
            )
        ]
        removed += len(inner) - len(survivors)
        if survivors:
            entry["hooks"] = survivors
            kept.append(entry)
    hooks["UserPromptSubmit"] = kept
    return removed


def install_v3_brief_hook(
    settings: dict[str, object],
    *,
    venv_python: Path,
    db_path: Path | None = None,
    vector_path: Path | None = None,
    workspace_id: str | None = None,
) -> None:
    """Add the v3 UserPromptSubmit brief hook (replaces the v2 inject_memory_context path).

    Idempotent: identifies prior entries by the ``v3-brief`` marker substring
    and overwrites them on re-run.
    """
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks
    ups = hooks.setdefault("UserPromptSubmit", [])
    if not isinstance(ups, list):
        ups = []
        hooks["UserPromptSubmit"] = ups
    cmd = f'"{venv_python}" "{V3_BRIEF_HOOK_SCRIPT}"'
    # Project-scoped installs bake the workspace into env via the hook script's
    # registry-walk — no extra args needed. We attach a tag comment so future
    # setup_agent runs can find and refresh this entry.
    marker = "agent-memory-lite-v3-brief"
    cmd = cmd + f" # {marker}"
    new_hook = {"type": "command", "command": cmd}
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
    if existing is None:
        ups.append({"hooks": [new_hook]})
    else:
        existing["hooks"] = [new_hook]


def install_v3_postedit_hook(
    settings: dict[str, object], *, venv_python: Path
) -> None:
    """Add the v3 PostToolUse digest-queue hook on Edit/Write/NotebookEdit/MultiEdit.

    Idempotent: identifies prior entries by the ``v3-postedit`` marker.
    """
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks
    post = hooks.setdefault("PostToolUse", [])
    if not isinstance(post, list):
        post = []
        hooks["PostToolUse"] = post
    marker = "agent-memory-lite-v3-postedit"
    cmd = f'"{venv_python}" "{V3_POSTEDIT_HOOK_SCRIPT}" # {marker}'
    new_entry = {
        "matcher": V3_POSTTOOLUSE_MATCHER,
        "hooks": [{"type": "command", "command": cmd}],
    }
    existing = next(
        (
            entry
            for entry in post
            if isinstance(entry, dict)
            and isinstance(entry.get("hooks"), list)
            and any(
                isinstance(h, dict) and marker in str(h.get("command", ""))
                for h in entry["hooks"]
            )
        ),
        None,
    )
    if existing is None:
        post.append(new_entry)
    else:
        existing["hooks"] = new_entry["hooks"]
        existing["matcher"] = V3_POSTTOOLUSE_MATCHER


# v3-only columns that ``CREATE TABLE IF NOT EXISTS`` skips for v2-shape
# tables. We patch them in explicitly via ALTER TABLE. Discovered when v3
# writer crashed on agentLight workspace:
# ``sqlite3.OperationalError: table decisions has no column named gist``.
# Add new entries here when v3 grows additional columns on shared kinds.
_V3_INPLACE_COLUMNS = (
    ("decisions", "gist", "TEXT"),
    ("theories", "gist", "TEXT"),
    ("episodes", "gist", "TEXT"),
)


def _apply_v3_inplace_columns(conn: Any) -> int:
    """ALTER TABLE add v3-only columns to v2-shape tables. Returns count added."""
    added = 0
    for table, column, sql_type in _V3_INPLACE_COLUMNS:
        try:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        except Exception:  # table may simply not exist yet (fresh v3 DB)
            continue
        if column in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
            added += 1
        except Exception as exc:  # surface but don't crash setup
            warn(f"could not ALTER {table} ADD {column}: {exc}")
    return added


def apply_v3_schema_and_seed(db_path: Path, *, workspace_id: str) -> None:
    """Apply v3 schema (idempotent IF NOT EXISTS) + seed 3 pinned discipline rules.

    Safe to call on a v2 DB: v3 adds new tables alongside v2 tables;
    existing tables are skipped by CREATE TABLE IF NOT EXISTS.  For
    columns that v3 adds to *existing* v2 tables (gist on decisions /
    theories / episodes), we patch via ALTER TABLE so the v3 writer
    can populate them.
    """
    import sqlite3  # noqa: PLC0415 — std-lib local import keeps top imports tidy

    if not V3_SCHEMA_PATH.exists():
        warn(f"v3 schema missing at {V3_SCHEMA_PATH} — skipping v3 deployment")
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(V3_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
        # Patch v3-only columns onto pre-existing v2-shape tables.
        added = _apply_v3_inplace_columns(conn)
        if added:
            conn.commit()
            ok(f"v3 inplace columns added (+{added}: gist on decisions/theories/episodes)")
        # Defer the seed import: keeps setup_agent's top imports clean,
        # and the script directory is added to sys.path below if needed.
        repo_root_str = str(REPO_ROOT)
        if repo_root_str not in sys.path:
            sys.path.insert(0, repo_root_str)
        from scripts.seed_v3_discipline import seed_discipline  # noqa: PLC0415

        results = seed_discipline(conn, workspace_id=workspace_id)
    except sqlite3.Error as exc:
        warn(f"v3 schema/seed failed: {exc}")
        return
    finally:
        conn.close()
    inserted = sum(1 for r in results if r.status == "inserted")
    skipped = sum(1 for r in results if r.status == "skipped")
    ok(
        f"v3 schema applied + discipline rules seeded "
        f"(inserted={inserted}, skipped={skipped}, "
        f"total={len(results)})"
    )


def seed_memory_bootstrap(
    python_exe: Path,
    *,
    db_path: Path,
    workspace_id: str,
) -> None:
    proc = subprocess.run(
        [
            str(python_exe),
            str(REPO_ROOT / "scripts" / "seed_project_memory.py"),
            "--workspace",
            workspace_id,
            "--db-path",
            str(db_path),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        if proc.stdout:
            info(f"seed stdout tail: {proc.stdout[-1000:]}")
        if proc.stderr:
            info(f"seed stderr tail: {proc.stderr[-1000:]}")
        proc.check_returncode()
    ok("neutral memory bootstrap seeded (skills/playbooks/concepts only; no behavior instructions)")


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
    """Render the canonical contract block. Self-contained: starts with the
    begin marker, ends with the end marker + trailing newline, no leading
    newline. The exact same string is what `upsert_contract` writes both on
    create and on update so the function is byte-stable across reruns.
    """
    body = CONTRACT_PATH.read_text(encoding="utf-8").rstrip()
    return f"{MARKER_BEGIN}\n\n{body}\n\n{MARKER_END}\n"


def sync_repo_contracts() -> int:
    """Sync this repo's own CLAUDE.md and AGENTS.md with docs/AGENT_CONTRACT.md."""
    section("Sync repo contracts (CLAUDE.md + AGENTS.md from docs/AGENT_CONTRACT.md)")
    for path in (REPO_ROOT / "CLAUDE.md", REPO_ROOT / "AGENTS.md"):
        status = upsert_contract(path)
        ok(f"{path.relative_to(REPO_ROOT)} -> {status}")
    return 0


def upsert_contract(path: Path) -> str:
    """Insert or replace the contract block in `path`. Returns 'created' / 'updated' / 'unchanged'.

    Invariants:

    * Idempotent: a synced file produces `unchanged` on every subsequent run.
    * Self-healing: the replaced span runs from the FIRST `MARKER_BEGIN` to
      the LAST `MARKER_END`, so a hand-broken file with stray duplicate
      markers is normalized in a single pass.
    * Preserves user content above the first `:begin` and below the last
      `:end`, separated from the block by exactly one blank line.
    """
    block = render_contract_block()
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text(block, encoding="utf-8")
        return "created"

    existing = path.read_text(encoding="utf-8")
    begin = existing.find(MARKER_BEGIN)
    if begin == -1:
        prefix = existing.rstrip()
        sep = "\n\n" if prefix else ""
        new = prefix + sep + block
    else:
        end = existing.rfind(MARKER_END)
        prefix = existing[:begin].rstrip()
        suffix = (
            existing[end + len(MARKER_END) :].lstrip("\n") if end != -1 and end >= begin else ""
        )
        prefix_sep = "\n\n" if prefix else ""
        suffix_sep = "\n" if suffix else ""
        new = prefix + prefix_sep + block + suffix_sep + suffix

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
    # In project mode (project_root + workspace_id present), strict isolation is
    # the secure default: the MCP server refuses to read/write any workspace_id
    # other than the project's own. The user can lift this by opening a chat in
    # the parent ("hub") directory or by exporting
    # MEMORY_STRICT_WORKSPACE_ISOLATION=false in the project shell.
    env: dict[str, str] = {"OLLAMA_PROBE_SKIP": os.environ.get("OLLAMA_PROBE_SKIP", "false")}
    if workspace_id:
        env["MEMORY_WORKSPACE_ID"] = workspace_id
        if workspace_id != "default":
            env["MEMORY_FORBID_DEFAULT_WORKSPACE"] = "true"
    if project_root is not None:
        env["MEMORY_DB_PATH"] = str(project_root / ".agent_memory" / "memory.db")
        env["VECTOR_DB_PATH"] = str(project_root / ".agent_memory" / "vectors.lance")
        if workspace_id and workspace_id != "default":
            env["MEMORY_STRICT_WORKSPACE_ISOLATION"] = "true"
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
        # v3.0.0 is the canonical surface; evict the legacy v2
        # inject_memory_context hook on every run so we don't double-emit
        # memory context on each UserPromptSubmit.
        evicted = _remove_v2_inject_hook(settings)
        if evicted:
            ok(f"removed legacy v2 inject hook ({evicted} entr{'y' if evicted == 1 else 'ies'})")

        pretooluse_status = install_pre_tool_use_hook(
            settings, venv_python=diag.venv_python, hook_script=PRETOOLUSE_HOOK_SCRIPT
        )
        ok(f"PreToolUse enforcement hook {pretooluse_status} (blocks rule-violating tool calls)")

        # v3.0.0 discipline stack — global mode (no per-project DB args; the
        # v3 brief hook resolves the workspace via the registry walker).
        install_v3_brief_hook(settings, venv_python=diag.venv_python)
        install_v3_postedit_hook(settings, venv_python=diag.venv_python)
        ok("v3 brief + PostToolUse digest hooks installed (Phase 5 discipline stack)")
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


def register_workspace_in_hub(
    *,
    workspace_id: str,
    db_path: Path,
    vector_path: Path,
    project_root: Path,
) -> None:
    """Append/update the workspace entry in the hub registry.

    Uses the in-process registry (no HTTP), so this works even when the
    shared service is down. The hub UI picks up new entries on its next
    state poll.
    """
    try:
        # Imported lazily so `setup_agent.py` keeps working in environments
        # where the package was not yet pip-installed.
        from agent_memory_lite.config.workspace_registry import WorkspaceRegistry  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - defensive
        warn(f"hub registry not updated ({exc.__class__.__name__}: {exc})")
        return

    registry_path = Path.home() / ".agent_memory" / "workspaces.json"
    registry_env = os.environ.get("MEMORY_WORKSPACES_FILE")
    if registry_env:
        registry_path = Path(registry_env)
    registry = WorkspaceRegistry(registry_path)
    registry.register(
        workspace_id=workspace_id,
        db_path=str(db_path),
        vector_path=str(vector_path),
        label=project_root.name,
        project_root=str(project_root),
    )
    ok(f"registered {workspace_id} in hub registry at {registry_path}")


def configure_project(  # noqa: PLR0912, PLR0915
    diag: Diagnosis,
    project_root: Path,
    *,
    workspace_id: str,
    seed_bootstrap: bool,
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

    # v3.0.0 is canonical — evict the legacy v2 inject hook if it was
    # installed by an older setup_agent run.  Idempotent.
    evicted = _remove_v2_inject_hook(settings)
    if evicted:
        ok(f"removed legacy v2 inject hook from project ({evicted} entries)")

    # v3.0.0 discipline stack — the only UserPromptSubmit + PostToolUse
    # surface installed by this version.
    install_v3_brief_hook(
        settings,
        venv_python=diag.venv_python,
        db_path=db_path,
        vector_path=vector_path,
        workspace_id=workspace_id,
    )
    install_v3_postedit_hook(settings, venv_python=diag.venv_python)

    pretooluse_status = install_pre_tool_use_hook(
        settings, venv_python=diag.venv_python, hook_script=PRETOOLUSE_HOOK_SCRIPT
    )
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    ok(f"MCP entry + project-scoped hook written to {settings_path}")
    ok("v3 brief + PostToolUse digest hooks installed (Phase 5 discipline stack)")
    ok(f"PreToolUse enforcement hook {pretooluse_status} (blocks rule-violating tool calls)")

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
        if workspace_id != "default":
            env["MEMORY_FORBID_DEFAULT_WORKSPACE"] = "true"
        subprocess.run(
            [str(diag.venv_python), str(REPO_ROOT / "scripts" / "bootstrap_db.py")],
            check=True,
            cwd=str(project_root),
            env=env,
        )
        ok(f"bootstrapped {db_path}")
    register_workspace_in_hub(
        workspace_id=workspace_id,
        db_path=db_path,
        vector_path=project_root / ".agent_memory" / "vectors.lance",
        project_root=project_root,
    )
    if seed_bootstrap:
        seed_memory_bootstrap(diag.venv_python, db_path=db_path, workspace_id=workspace_id)

    # v3.0.0 — apply v3 schema (idempotent) + seed the 3 pinned discipline
    # rules so every Claude Code session in this project sees the graph-tools-
    # first / search-before-write / capability-link-on-write rules in the
    # brief's behaviors section.
    apply_v3_schema_and_seed(db_path, workspace_id=workspace_id)

    hook_result = install_project_pre_commit_hook(
        repo_root=REPO_ROOT, project_root=project_root, workspace_id=workspace_id
    )
    status = hook_result["status"]
    hook_path = hook_result.get("hook_path")
    if status == "installed":
        ok(f"pre-commit auto-ingest hook installed at {hook_path}")
    elif status == "refreshed":
        ok(f"pre-commit auto-ingest hook refreshed at {hook_path} (workspace_id={workspace_id})")
    elif status == "unchanged":
        info(f"pre-commit auto-ingest hook already up to date at {hook_path}")
    elif status == "skipped_no_git":
        info(f"pre-commit auto-ingest hook skipped: {project_root} is not a git working tree")
    elif status == "skipped_no_template":
        warn("pre-commit auto-ingest hook template missing in repo (scripts/git_hooks/pre-commit)")


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


def main() -> int:  # noqa: PLR0912, PLR0915 - linear CLI argparser; readable as one block
    parser = argparse.ArgumentParser(description="Configure AI agents on this machine.")
    parser.add_argument("--check-only", action="store_true", help="Diagnose, do not write.")
    parser.add_argument("--no-hook", action="store_true", help="Skip Claude Code hook install.")
    parser.add_argument("--yes", action="store_true", help="Non-interactive (assume yes).")
    parser.add_argument(
        "--workspace",
        default=None,
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
    parser.add_argument(
        "--no-seed-memory-bootstrap",
        action="store_true",
        help=(
            "Skip the neutral memory-population seed. By default setup writes only "
            "generic skills/playbooks/concepts that help agents populate memory; it "
            "does not write behavior instructions, style, language, or project roles."
        ),
    )
    parser.add_argument(
        "--sync-repo",
        action="store_true",
        help=(
            "Sync the repo's own CLAUDE.md and AGENTS.md to match the canonical "
            "docs/AGENT_CONTRACT.md (between the agent-memory-lite-contract markers). "
            "Use this after editing AGENT_CONTRACT.md so both anchor files stay "
            "consistent. CI runs the same sync and fails if `git diff` is dirty."
        ),
    )
    args = parser.parse_args()

    if args.sync_repo:
        return sync_repo_contracts()

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
        workspace_id = args.workspace or project_root.name
        configure_project(
            diag,
            project_root,
            workspace_id=workspace_id,
            seed_bootstrap=not args.no_seed_memory_bootstrap,
        )
        section("Done (project mode)")
        print(
            f"This project ({project_root.name}) now has its own memory at\n"
            f"  {project_root / '.agent_memory'}\n"
            f"using workspace_id={workspace_id!r}, and its own CLAUDE.md / AGENTS.md contract.\n"
            "Restart your agent runtime and it will see ONLY this project's memory.\n"
        )
        smoke_test_mcp(diag)
        return 0

    section("Bootstrap database")
    global_workspace_id = args.workspace or os.environ.get("MEMORY_WORKSPACE_ID", "default")
    if not diag.db_exists:
        bootstrap_db()
        ok("database created")
    else:
        ok("database already present")
    if not args.no_seed_memory_bootstrap:
        assert diag.db_path is not None
        seed_memory_bootstrap(
            diag.venv_python,
            db_path=diag.db_path,
            workspace_id=global_workspace_id,
        )

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
