"""Isolated tmp workspace lifecycle for the crash test.

Creates a fresh ``qa-crash-test`` workspace under
``tests/qa/qa-crash-test/`` (gitignored) with its own SQLite + LanceDB
pair. Registers in the shared workspace registry so the running HTTP
service can route to it. On teardown: removes the registry entry and
deletes the on-disk files. NEVER touches the project's main workspace.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
QA_ROOT = REPO_ROOT / "tests" / "qa" / "qa-crash-test"


def workspace_paths() -> tuple[Path, Path, Path]:
    """Return (workspace_dir, db_path, vector_path) for the crash-test workspace."""
    return (
        QA_ROOT,
        QA_ROOT / ".agent_memory" / "memory.db",
        QA_ROOT / ".agent_memory" / "vectors.lance",
    )


def ensure_clean_workspace() -> None:
    """Strict reset: if the workspace dir exists, blow it away. The crash test
    must start from a known-empty state."""
    if QA_ROOT.exists():
        shutil.rmtree(QA_ROOT, ignore_errors=True)
    (QA_ROOT / ".agent_memory").mkdir(parents=True, exist_ok=True)


def bootstrap_db(*, workspace_id: str, db_path: Path, vector_path: Path) -> None:
    """Run the project's bootstrap_db script against the test paths."""
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "bootstrap_db.py"),
        ],
        env={
            **_passthrough_env(),
            "MEMORY_WORKSPACE_ID": workspace_id,
            "MEMORY_DB_PATH": str(db_path),
            "VECTOR_DB_PATH": str(vector_path),
            "OLLAMA_PROBE_SKIP": "true",
            "MEMORY_FORBID_DEFAULT_WORKSPACE": "false",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"bootstrap_db failed: {proc.returncode}\n"
            f"stderr: {proc.stderr[-500:]}\n"
            f"stdout: {proc.stdout[-500:]}"
        )


def register_in_registry(*, workspace_id: str, db_path: Path, vector_path: Path) -> None:
    """Add to the user-level workspaces.json so HTTP service can route."""
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "register_workspace.py"),
            "register",
            "--workspace",
            workspace_id,
            "--db-path",
            str(db_path),
            "--vector-path",
            str(vector_path),
            "--label",
            "QA crash-test (transient)",
            "--project",
            str(QA_ROOT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"register_workspace failed: {proc.stderr[-500:]}")


def remove_from_registry(*, workspace_id: str) -> None:
    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "register_workspace.py"),
            "remove",
            "--workspace",
            workspace_id,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def teardown_workspace() -> None:
    """Idempotent teardown: registry remove + rmtree."""
    if QA_ROOT.exists():
        shutil.rmtree(QA_ROOT, ignore_errors=True)


def _passthrough_env() -> dict[str, str]:
    keep = (
        "PATH",
        "PYTHONPATH",
        "SystemRoot",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOME",
        "APPDATA",
        "LOCALAPPDATA",
    )
    return {k: os.environ[k] for k in keep if k in os.environ}
