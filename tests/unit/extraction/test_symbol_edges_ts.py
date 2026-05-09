"""1.5.2: per-language tree-sitter edge extractor tests.

Each test skips gracefully if the grammar package is missing, so
the test suite stays portable across environments that did or did
not install the optional ``[tree-sitter]`` extras.
"""

from __future__ import annotations

import pytest

from agent_memory_lite.chunking.ts_grammar import is_supported
from agent_memory_lite.extraction.symbol_edges_ts import extract_ts_edges


def _require(lang: str) -> None:
    if not is_supported(lang):
        pytest.skip(f"tree-sitter grammar for {lang} is not installed")


def _by_type(edges: list, edge_type: str) -> list:
    return [e for e in edges if e.edge_type == edge_type]


def test_javascript_calls_owned_by_method() -> None:
    _require("javascript")
    src = (
        "function helper() { return 1; }\n"
        "class Svc {\n"
        "  fetch() {\n"
        "    helper();\n"
        "    other.run();\n"
        "  }\n"
        "}\n"
    )
    edges = extract_ts_edges(src, "javascript")
    calls = _by_type(edges, "calls")
    pairs = {(e.src_qualified_name, e.dst_qualified_name) for e in calls}
    assert ("Svc.fetch", "helper") in pairs
    assert ("Svc.fetch", "other.run") in pairs


def test_typescript_imports_emit_module_owner() -> None:
    _require("typescript")
    src = "import { Helper } from './helper';\n"
    edges = extract_ts_edges(src, "typescript")
    imports = _by_type(edges, "imports")
    assert all(e.src_qualified_name == "<module>" for e in imports)
    targets = {e.dst_qualified_name for e in imports}
    assert "Helper" in targets or "./helper" in targets


def test_typescript_new_expression_is_instantiates() -> None:
    _require("typescript")
    src = "class Foo {\n  static make() { return new Foo(); }\n}\n"
    edges = extract_ts_edges(src, "typescript")
    inst = _by_type(edges, "instantiates")
    assert any(e.src_qualified_name == "Foo.make" and e.dst_qualified_name == "Foo" for e in inst)


def test_go_calls_inside_function() -> None:
    _require("go")
    src = (
        "package foo\n"
        'import "fmt"\n'
        "func Hello() {\n"
        '    fmt.Println("x")\n'
        "    helper()\n"
        "}\n"
        "func helper() {}\n"
    )
    edges = extract_ts_edges(src, "go")
    calls = _by_type(edges, "calls")
    targets = {e.dst_qualified_name for e in calls if e.src_qualified_name == "Hello"}
    assert "fmt.Println" in targets
    assert "helper" in targets
    imports = _by_type(edges, "imports")
    assert any(e.dst_qualified_name == "fmt" for e in imports)


def test_rust_calls_and_use() -> None:
    _require("rust")
    src = 'use std::io::Write;\nfn main() {\n    println!("x");\n    helper();\n}\nfn helper() {}\n'
    edges = extract_ts_edges(src, "rust")
    targets = {e.dst_qualified_name for e in edges if e.src_qualified_name == "main"}
    # macro_invocation 'println' OR call_expression 'helper' — at least
    # the bare helper() must show up as a calls edge owner=main.
    assert "helper" in targets
    imports = _by_type(edges, "imports")
    # Rust use std::io::Write — the walker collects the trailing
    # identifier 'Write' (and possibly intermediates 'std', 'io').
    assert imports, "expected at least one imports edge from `use` decl"


def test_java_method_invocation_and_imports() -> None:
    _require("java")
    src = (
        "import java.util.List;\n"
        "class Greeter {\n"
        "  public void hi() {\n"
        '    System.out.println("x");\n'
        "  }\n"
        "}\n"
    )
    edges = extract_ts_edges(src, "java")
    calls = _by_type(edges, "calls")
    # Greeter.hi calls System.out.println — the call_target_name walker
    # picks up an identifier in the call subtree.
    owners = {e.src_qualified_name for e in calls}
    assert "Greeter.hi" in owners
    imports = _by_type(edges, "imports")
    assert any("List" in e.dst_qualified_name or e.dst_qualified_name == "List" for e in imports)


def test_cpp_call_and_include() -> None:
    _require("cpp")
    src = (
        "#include <vector>\n"
        "int helper() { return 0; }\n"
        "int main() {\n"
        "    helper();\n"
        "    return 0;\n"
        "}\n"
    )
    edges = extract_ts_edges(src, "cpp")
    calls = _by_type(edges, "calls")
    pairs = {(e.src_qualified_name, e.dst_qualified_name) for e in calls}
    assert ("main", "helper") in pairs
    imports = _by_type(edges, "imports")
    assert any("vector" in e.dst_qualified_name for e in imports)


def test_csharp_invocation_and_using() -> None:
    _require("csharp")
    src = (
        "using System;\n"
        "class Greeter {\n"
        "  public void Hi() {\n"
        '    Console.WriteLine("x");\n'
        "  }\n"
        "}\n"
    )
    edges = extract_ts_edges(src, "csharp")
    calls = _by_type(edges, "calls")
    owners = {e.src_qualified_name for e in calls}
    assert "Greeter.Hi" in owners
    imports = _by_type(edges, "imports")
    assert any(e.dst_qualified_name == "System" for e in imports)


def test_unsupported_language_returns_empty() -> None:
    assert extract_ts_edges("(defun add (a b) (+ a b))", "lisp") == []


def test_unparseable_input_returns_empty() -> None:
    """Tree-sitter is forgiving but pathologically malformed input
    should still not crash the extractor."""
    out = extract_ts_edges("\x00\x01\x02", "javascript")
    # Either empty list (no symbols recovered) or a small list — must
    # not raise.
    assert isinstance(out, list)
