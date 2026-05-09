"""1.6.0: signature extractor unit tests."""

from __future__ import annotations

from agent_memory_lite.extraction.signature_extractor import (
    content_hash,
    extract_signature,
    signature_hash,
)


def test_python_def_first_line() -> None:
    src = "def foo(x, y):\n    return x + y\n"
    assert extract_signature(src) == "def foo(x, y):"


def test_python_skips_decorator_and_docstring() -> None:
    src = '@cache\ndef slow(x):\n    """docstring"""\n    return x\n'
    assert extract_signature(src) == "def slow(x):"


def test_class_def_returned() -> None:
    src = "class Foo(Base):\n    pass\n"
    assert extract_signature(src) == "class Foo(Base):"


def test_typescript_method_signature() -> None:
    src = "  async fetch(id: number): Promise<User> {\n    return this.db.get(id);\n  }\n"
    assert extract_signature(src) == "async fetch(id: number): Promise<User> {"


def test_skips_comment_only_prefix() -> None:
    src = "// comment\n# comment\nfn main() {}\n"
    assert extract_signature(src) == "fn main() {}"


def test_skips_rust_attribute() -> None:
    src = "#[test]\nfn add() {}\n"
    assert extract_signature(src) == "fn add() {}"


def test_signature_hash_stable() -> None:
    h1 = signature_hash("def foo(x, y):")
    h2 = signature_hash("  def foo(x, y):  ")  # padding stripped
    assert h1 == h2


def test_signature_hash_changes_on_param_change() -> None:
    h1 = signature_hash("def foo(x):")
    h2 = signature_hash("def foo(x, y):")
    assert h1 != h2


def test_content_hash_changes_on_body_change() -> None:
    h1 = content_hash("def foo():\n    return 1\n")
    h2 = content_hash("def foo():\n    return 2\n")
    assert h1 != h2


def test_empty_input_returns_empty_signature() -> None:
    assert extract_signature("") == ""
    assert extract_signature("   \n  \n") == ""
