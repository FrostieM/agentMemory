from __future__ import annotations

from pathlib import Path

import pytest

from agent_memory_lite.utils.pathing import normalize_path, to_workspace_relative


def test_normalize_returns_forward_slashes(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c.txt"
    nested.parent.mkdir(parents=True)
    nested.write_text("x")
    out = normalize_path(nested)
    assert "\\" not in out
    assert out.endswith("a/b/c.txt")


def test_normalize_resolves_relative_against_base(tmp_path: Path) -> None:
    out = normalize_path("nested/file.py", base=tmp_path)
    assert out.endswith("nested/file.py")
    assert out.startswith(str(tmp_path).replace("\\", "/"))


def test_to_workspace_relative_strips_root(tmp_path: Path) -> None:
    f = tmp_path / "a" / "b.py"
    f.parent.mkdir()
    f.write_text("x")
    rel = to_workspace_relative(f, tmp_path)
    assert rel == "a/b.py"


def test_to_workspace_relative_rejects_outside_paths(tmp_path: Path) -> None:
    other = tmp_path.parent
    with pytest.raises(ValueError, match="not in the subpath"):
        to_workspace_relative(other, tmp_path)
