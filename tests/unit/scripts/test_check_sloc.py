"""Batch G: SLOC ratchet gate tests.

The ratchet's whole point is that grandfathered oversized files can only SHRINK.
These drive check_sloc.main() against a synthetic source tree + baseline (module
globals monkeypatched) so the gate logic is pinned deterministically.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts import check_sloc


def _write_py(path: Path, sloc: int) -> None:
    """Write a file with exactly ``sloc`` non-blank, non-comment lines."""
    path.write_text("\n".join(f"x{i} = {i}" for i in range(sloc)) + "\n", encoding="utf-8")


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src = tmp_path / "src"
    src.mkdir()
    baseline = tmp_path / "sloc_baseline.json"
    monkeypatch.setattr(check_sloc, "SRC_ROOT", src)
    monkeypatch.setattr(check_sloc, "BASELINE_PATH", baseline)
    return src, baseline


def test_within_cap_passes(tree, capsys: pytest.CaptureFixture[str]) -> None:
    src, baseline = tree
    _write_py(src / "big.py", 200)
    baseline.write_text(json.dumps({"big.py": 200}))
    assert check_sloc.main(["--enforce"]) == 0


def test_grandfathered_file_that_grew_fails(tree, capsys: pytest.CaptureFixture[str]) -> None:
    src, baseline = tree
    _write_py(src / "big.py", 210)  # cap is 200 -> grew by 10
    baseline.write_text(json.dumps({"big.py": 200}))
    assert check_sloc.main(["--enforce"]) == 1
    assert "GREW past their frozen cap" in capsys.readouterr().out


def test_new_oversized_file_fails(tree, capsys: pytest.CaptureFixture[str]) -> None:
    src, baseline = tree
    _write_py(src / "newbig.py", 200)
    baseline.write_text(json.dumps({}))
    assert check_sloc.main(["--enforce"]) == 1
    assert "NEW file(s)" in capsys.readouterr().out


def test_stale_baseline_entry_fails(tree, capsys: pytest.CaptureFixture[str]) -> None:
    """A baselined file decomposed to <= 150 must be REMOVED from the baseline so
    the ratchet set shrinks -- leaving it in is a hard fail."""
    src, baseline = tree
    _write_py(src / "shrunk.py", 100)  # now under the ceiling
    baseline.write_text(json.dumps({"shrunk.py": 200}))
    assert check_sloc.main(["--enforce"]) == 1
    assert "remove from baseline" in capsys.readouterr().out


def test_enforce_off_never_exits_nonzero(tree) -> None:
    src, baseline = tree
    _write_py(src / "big.py", 210)
    baseline.write_text(json.dumps({"big.py": 200}))
    assert check_sloc.main([]) == 0  # warns/prints but no --enforce -> exit 0


def test_write_baseline_refuses_new_offender(tree, capsys: pytest.CaptureFixture[str]) -> None:
    """--write-baseline must not let a newly-grown file be baselined as debt."""
    src, baseline = tree
    _write_py(src / "old.py", 200)
    _write_py(src / "new.py", 180)
    baseline.write_text(json.dumps({"old.py": 200}))  # new.py is a fresh offender
    assert check_sloc.main(["--write-baseline"]) == 1
    assert "refusing to baseline" in capsys.readouterr().out


def test_write_baseline_ratchets_down(tree) -> None:
    """--write-baseline lowers a cap to the current size and drops files now <=150."""
    src, baseline = tree
    _write_py(src / "shrinking.py", 180)  # was capped at 200
    _write_py(src / "gone.py", 100)  # decomposed below the ceiling
    baseline.write_text(json.dumps({"shrinking.py": 200, "gone.py": 200}))
    assert check_sloc.main(["--write-baseline"]) == 0
    updated = json.loads(baseline.read_text())
    assert updated == {"shrinking.py": 180}  # cap lowered, gone.py dropped
