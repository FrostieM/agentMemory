"""Verify `setup_agent.py:upsert_contract` is idempotent and self-healing.

The function is the heart of the single-source agent contract: it injects
`docs/AGENT_CONTRACT.md` between the canonical markers in `CLAUDE.md` /
`AGENTS.md`. These tests pin the invariants that:

* a fresh write produces exactly one begin/end pair,
* re-running on a synced file is a no-op (`unchanged`),
* user content outside the markers is preserved,
* and a hand-broken file with a duplicate `:end` marker is healed by a
  single sync pass (instead of accumulating drift).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_setup(tmp_canonical: Path) -> ModuleType:
    repo_root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "setup_agent_upsert_under_test", repo_root / "scripts" / "setup_agent.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    # Redirect the canonical contract source to a tmp file so the test does
    # not depend on the live repo body length.
    module.CONTRACT_PATH = tmp_canonical
    return module


def _make_canonical(tmp_path: Path, body: str = "CANONICAL BODY") -> Path:
    canonical = tmp_path / "AGENT_CONTRACT.md"
    canonical.write_text(body, encoding="utf-8")
    return canonical


def _count_markers(text: str) -> tuple[int, int]:
    return (
        text.count("<!-- agent-memory-lite-contract:begin -->"),
        text.count("<!-- agent-memory-lite-contract:end -->"),
    )


def test_upsert_creates_single_block_on_empty_path(tmp_path: Path) -> None:
    setup = _load_setup(_make_canonical(tmp_path))
    target = tmp_path / "CLAUDE.md"

    status = setup.upsert_contract(target)

    assert status == "created"
    text = target.read_text(encoding="utf-8")
    assert _count_markers(text) == (1, 1)
    assert "CANONICAL BODY" in text


def test_upsert_is_idempotent(tmp_path: Path) -> None:
    setup = _load_setup(_make_canonical(tmp_path))
    target = tmp_path / "CLAUDE.md"

    setup.upsert_contract(target)
    second = setup.upsert_contract(target)

    assert second == "unchanged"
    assert _count_markers(target.read_text(encoding="utf-8")) == (1, 1)


def test_upsert_preserves_user_content_around_markers(tmp_path: Path) -> None:
    setup = _load_setup(_make_canonical(tmp_path, body="FRESH BODY"))
    target = tmp_path / "CLAUDE.md"
    target.write_text(
        "# user prologue\n\n"
        "<!-- agent-memory-lite-contract:begin -->\n"
        "OLD BODY\n"
        "<!-- agent-memory-lite-contract:end -->\n\n"
        "# user epilogue\n",
        encoding="utf-8",
    )

    status = setup.upsert_contract(target)

    assert status == "updated"
    text = target.read_text(encoding="utf-8")
    assert "# user prologue" in text
    assert "# user epilogue" in text
    assert "OLD BODY" not in text
    assert "FRESH BODY" in text
    assert _count_markers(text) == (1, 1)


def test_upsert_heals_duplicate_end_marker(tmp_path: Path) -> None:
    """A file hand-broken with a stray duplicate `:end` marker is normalized
    in one sync pass — the replaced span runs from the first `:begin` to the
    LAST `:end`, so any orphaned middle is consumed.
    """
    setup = _load_setup(_make_canonical(tmp_path, body="FRESH BODY"))
    target = tmp_path / "CLAUDE.md"
    target.write_text(
        "# user prologue\n\n"
        "<!-- agent-memory-lite-contract:begin -->\n"
        "OLD BODY\n"
        "<!-- agent-memory-lite-contract:end -->\n"
        "stray middle text that should not survive\n"
        "<!-- agent-memory-lite-contract:end -->\n\n"
        "# user epilogue\n",
        encoding="utf-8",
    )

    status = setup.upsert_contract(target)

    assert status == "updated"
    text = target.read_text(encoding="utf-8")
    # Exactly one begin/end pair after the heal.
    assert _count_markers(text) == (1, 1)
    # Stray middle is gone, real epilogue stays.
    assert "stray middle text" not in text
    assert "# user prologue" in text
    assert "# user epilogue" in text
    assert "FRESH BODY" in text


def test_upsert_dangling_begin_without_end_truncates_to_block(tmp_path: Path) -> None:
    """A file with `:begin` but no `:end` is treated as malformed: the begin
    anchor is honored, content after it is replaced by the fresh block.
    """
    setup = _load_setup(_make_canonical(tmp_path, body="FRESH BODY"))
    target = tmp_path / "CLAUDE.md"
    target.write_text(
        "# user prologue\n\n"
        "<!-- agent-memory-lite-contract:begin -->\n"
        "half-written body without a closing marker\n",
        encoding="utf-8",
    )

    status = setup.upsert_contract(target)

    assert status == "updated"
    text = target.read_text(encoding="utf-8")
    assert _count_markers(text) == (1, 1)
    assert "# user prologue" in text
    assert "half-written body" not in text
    assert "FRESH BODY" in text


def test_upsert_no_begin_appends_block_without_touching_user_content(tmp_path: Path) -> None:
    setup = _load_setup(_make_canonical(tmp_path, body="FRESH BODY"))
    target = tmp_path / "CLAUDE.md"
    target.write_text("# user prologue\n", encoding="utf-8")

    status = setup.upsert_contract(target)

    assert status == "updated"
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# user prologue")
    assert _count_markers(text) == (1, 1)
    assert "FRESH BODY" in text
