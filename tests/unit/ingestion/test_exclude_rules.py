from __future__ import annotations

from pathlib import Path

from agent_memory_lite.ingestion.exclude_rules import (
    BUILTIN_DENYLIST,
    collect_patterns,
    is_excluded,
)


def test_builtin_denylist_blocks_node_modules() -> None:
    patterns = collect_patterns(Path("/tmp"))
    assert is_excluded("node_modules/foo.js", is_dir=False, patterns=patterns)


def test_builtin_denylist_blocks_dot_git() -> None:
    patterns = collect_patterns(Path("/tmp"))
    assert is_excluded(".git/HEAD", is_dir=False, patterns=patterns)


def test_builtin_denylist_blocks_agent_worktrees() -> None:
    patterns = collect_patterns(Path("/tmp"))
    assert is_excluded(".claude/worktrees/old/src/app.py", is_dir=False, patterns=patterns)
    assert is_excluded(".codex/cache/tmp.py", is_dir=False, patterns=patterns)


def test_unmatched_path_passes() -> None:
    patterns = collect_patterns(Path("/tmp"))
    assert not is_excluded("src/main.py", is_dir=False, patterns=patterns)


def test_gitignore_loaded(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("secret/\n*.log\n")
    patterns = collect_patterns(tmp_path)
    assert is_excluded("secret/data.txt", is_dir=False, patterns=patterns)
    assert is_excluded("trace.log", is_dir=False, patterns=patterns)


def test_memoryignore_overrides(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("docs/\n")
    (tmp_path / ".memoryignore").write_text("!docs/important.md\n")
    patterns = collect_patterns(tmp_path)
    assert is_excluded("docs/notes.md", is_dir=False, patterns=patterns)
    assert not is_excluded("docs/important.md", is_dir=False, patterns=patterns)


def test_extra_patterns_stack() -> None:
    patterns = collect_patterns(Path("/tmp"), extra=["temp/"])
    assert is_excluded("temp/foo.txt", is_dir=False, patterns=patterns)


def test_min_js_pattern_blocks() -> None:
    patterns = collect_patterns(Path("/tmp"))
    assert is_excluded("static/app.min.js", is_dir=False, patterns=patterns)
    assert "*.min.js" in BUILTIN_DENYLIST


def test_builtin_denylist_blocks_audit_tmp_scratch() -> None:
    patterns = collect_patterns(Path("/tmp"))
    # root-level _audit_tmp file matches via the first-component dir rule
    assert is_excluded("_audit_tmp/fuzz_symlink_cycle.py", is_dir=False, patterns=patterns)
    # a nested _audit_tmp dir is dropped by the scanner's dir-level check
    assert is_excluded("src/_audit_tmp", is_dir=True, patterns=patterns)


def test_builtin_denylist_blocks_tmp_scratch_files() -> None:
    patterns = collect_patterns(Path("/tmp"))
    assert is_excluded("_tmp_repro_churn.py", is_dir=False, patterns=patterns)
    assert is_excluded("_tmp_probe_brainfail.py", is_dir=False, patterns=patterns)
    # basename glob catches them at any depth too
    assert is_excluded("src/_tmp_repro_x.py", is_dir=False, patterns=patterns)


def test_scratch_patterns_spare_legit_source() -> None:
    """The scratch globs must NOT swallow ordinary source files/dirs."""
    patterns = collect_patterns(Path("/tmp"))
    assert not is_excluded("src/main.py", is_dir=False, patterns=patterns)
    assert not is_excluded("tmp_data.py", is_dir=False, patterns=patterns)
    assert not is_excluded("my_tmp_file.py", is_dir=False, patterns=patterns)
    assert not is_excluded("src/template/helper.py", is_dir=False, patterns=patterns)
    assert not is_excluded("contemplate/x.py", is_dir=False, patterns=patterns)
