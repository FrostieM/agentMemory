"""Sector 8 Round-2 adversarial audit — regression locks.

A fresh adversarial agent audited the hooks & scripts:
  HIGH   — pre-commit hook (jq-absent fallback) spliced a staged file
           path into a `python -c` source string: RCE on `git commit`
  MEDIUM — registry db_path / vector_path fed to sqlite3.connect /
           the X-Memory-DB-Path header with zero validation
  LOW    — transcript_pair_extractor._read_tail could read an entire
           multi-GB newline-free file into memory

This file locks each fix so a re-audit finds nothing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PRE_COMMIT = REPO_ROOT / "scripts" / "git_hooks" / "pre-commit"


# ---------- HIGH: pre-commit command injection ----------


def test_precommit_has_no_string_interpolated_python_c() -> None:
    """Static lock: the pre-commit hook must NOT splice a shell variable
    into a Python source literal. The smoking gun is a shell var inside
    single quotes — `'$path'`, `'$WORKSPACE_ID'`, `'$lang'` — which is
    exactly the RCE shape. If someone reverts to it, this fails."""
    text = PRE_COMMIT.read_text(encoding="utf-8")
    for danger in ("'$path'", "'$WORKSPACE_ID'", "'$lang'", "'$ext'"):
        assert danger not in text, (
            f"pre-commit splices {danger} into a Python literal — that is "
            "the RCE. Pass the value via argv through a single-quoted heredoc."
        )
    # The safe shape must be present: argv invocation + single-quoted heredoc.
    assert "python - " in text, "pre-commit must invoke python via argv (`python - ...`)"
    assert "<<'PYEOF'" in text, "pre-commit must use a single-quoted heredoc for the script"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
def test_precommit_payload_build_does_not_execute_hostile_filename(
    tmp_path: Path,
) -> None:
    """Behavioral lock: replicate the hook's jq-absent payload build with
    a maliciously-named file. The Python injection attempt must be
    treated as inert data — no marker file is created."""
    marker = tmp_path / "PWNED"
    # A filename crafted to break out of a single-quoted Python literal.
    hostile = "'; __import__('os').system('echo x > " + str(marker).replace("\\", "/") + "'); '"
    target = tmp_path / "real.py"
    target.write_text("print('hello')", encoding="utf-8")

    # The exact heredoc form the fixed hook uses.
    script = (
        'payload=$(python - "$1" "$2" "$3" <<\'PYEOF\' 2>/dev/null || true\n'
        "import json, pathlib, sys\n"
        "ws, path, lang = sys.argv[1], sys.argv[2], sys.argv[3]\n"
        "try:\n"
        '    content = pathlib.Path(path).read_text(encoding="utf-8")\n'
        "except Exception:\n"
        '    content = ""\n'
        'print(json.dumps({"workspace_id": ws, "path": path, '
        '"content": content, "language": lang}))\n'
        "PYEOF\n"
        ")\n"
        'echo "len=${#payload}"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script, "bash", "ws", hostile, "python"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert not marker.exists(), "hostile filename executed code — the RCE is back"
    # The payload still builds (the path is just inert data inside it).
    assert "len=" in result.stdout


# ---------- MEDIUM: registry db_path validation ----------


def test_safe_registry_path_rejects_malicious_shapes() -> None:
    """_safe_registry_path must reject UNC / network / null-byte /
    relative paths — shapes never used by a legitimate local DB."""
    from scripts.inject_memory_context import _safe_registry_path  # noqa: PLC0415

    # Rejected → empty string.
    assert _safe_registry_path(r"\\evil-server\share\memory.db") == ""
    assert _safe_registry_path("//evil-server/share/memory.db") == ""
    assert _safe_registry_path("relative/path/memory.db") == ""
    assert _safe_registry_path("memory.db") == ""
    assert _safe_registry_path("with\x00null.db") == ""
    assert _safe_registry_path("") == ""
    assert _safe_registry_path("   ") == ""


def test_safe_registry_path_accepts_legitimate_absolute_paths() -> None:
    """A normal absolute project DB path must pass — confinement to a
    fixed root is deliberately NOT done (projects live anywhere)."""
    from scripts.inject_memory_context import _safe_registry_path  # noqa: PLC0415

    # Both POSIX and Windows absolute shapes are legitimate.
    posix = "/home/user/work/proj/.agent_memory/memory.db"
    win = r"C:\Users\dev\work\proj\.agent_memory\memory.db"
    # At least one must round-trip on this platform; os.path.isabs is
    # platform-specific, so accept whichever the host recognises.
    import os  # noqa: PLC0415

    if os.path.isabs(posix):
        assert _safe_registry_path(posix) == posix
    if os.path.isabs(win):
        assert _safe_registry_path(win) == win


def test_brief_and_pretooluse_hooks_have_the_guard() -> None:
    """The same guard must exist in the other two registry-reading
    hooks — a re-audit checks all three."""
    for name in ("inject_memory_brief.py", "pre_tool_use_check.py"):
        text = (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "_safe_registry_path" in text, f"{name} missing the db_path guard"


# ---------- LOW: _read_tail byte ceiling ----------


def test_read_tail_bounded_on_newline_free_file(tmp_path: Path) -> None:
    """A giant newline-free transcript must not stream entirely into
    memory — _read_tail caps the read at 8 MB."""
    from scripts.transcript_pair_extractor import _read_tail  # noqa: PLC0415

    big = tmp_path / "huge.jsonl"
    # 12 MB single line, no newline — past the 8 MB ceiling.
    big.write_bytes(b"x" * (12 * 1024 * 1024))
    lines = _read_tail(big, tail_lines=50)
    # It returns *something* without hanging; the joined content must be
    # bounded well under the full 12 MB file.
    joined = "".join(lines)
    assert len(joined) <= 9 * 1024 * 1024, (
        f"_read_tail returned {len(joined)} bytes — the 8 MB ceiling leaked"
    )
