"""Batch I: UTF-8 / mojibake encoding gate tests.

Includes a false-positive guard: legitimate non-ASCII UTF-8 (the project's v1.10
Cyrillic, accented latin) must NOT be flagged. All non-ASCII fixtures are built
via chr() so this test source stays pure-ASCII (and never self-flags).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import check_encoding

_FFFD = chr(0xFFFD)  # U+FFFD replacement character
# Legit UTF-8 (NOT mojibake): Cyrillic word + an accented latin letter.
_LEGIT = "".join(chr(c) for c in (0x0420, 0x0435, 0x0448, 0x0435, 0x043D, 0x00E9))


def _src(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setattr(check_encoding, "ROOT", tmp_path)
    return src


def test_committed_tree_is_clean() -> None:
    """The real repo must be free of mojibake."""
    assert check_encoding.scan() == []


def test_clean_ascii_file_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _src(tmp_path, monkeypatch)
    (src / "clean.py").write_text("x = 1  # plain ascii\n", encoding="utf-8")
    assert check_encoding.scan() == []


def test_legit_non_ascii_is_not_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Legitimate UTF-8 (Cyrillic + accented latin) must not false-positive."""
    src = _src(tmp_path, monkeypatch)
    (src / "ru.py").write_text(f"# {_LEGIT}\n", encoding="utf-8")
    assert check_encoding.scan() == []


def test_replacement_char_is_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _src(tmp_path, monkeypatch)
    (src / "lossy.py").write_text(f"# {_FFFD}\n", encoding="utf-8")
    assert any("lossy.py" in rel for rel, _ in check_encoding.scan())


def test_double_encoded_smart_quote_is_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _src(tmp_path, monkeypatch)
    # A smart quote (U+2019) whose UTF-8 bytes were mis-decoded as cp1252.
    mojibake = chr(0x2019).encode("utf-8").decode("cp1252")
    (src / "bad.py").write_text(f"# wasn{mojibake}t\n", encoding="utf-8")
    assert any("bad.py" in rel for rel, _ in check_encoding.scan())


def test_enforce_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _src(tmp_path, monkeypatch)
    (src / "lossy.py").write_text(f"{_FFFD}\n", encoding="utf-8")
    assert check_encoding.main(["--enforce"]) == 1
    assert check_encoding.main([]) == 0  # report-only never fails
