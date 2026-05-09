"""1.4.0: per-language tree-sitter symbol chunking.

Each language has a small fixture exercising the structural shapes
the dispatcher needs to handle — top-level functions, classes,
methods inside classes, and where applicable the language-specific
constructs (struct / enum / interface / type alias).

These tests REQUIRE the optional tree-sitter grammar packages from
``[project.optional-dependencies] tree-sitter``. When unavailable
(e.g. CI that did not install the extras), the dispatcher falls
back to the legacy token-window split and the assertions on
``qualified_name`` would not hold; therefore each test skips
gracefully on a missing grammar.
"""

from __future__ import annotations

import pytest

from agent_memory_lite.chunking.code import chunk_code
from agent_memory_lite.chunking.ts_grammar import is_supported


def _require(lang: str) -> None:
    if not is_supported(lang):
        pytest.skip(f"tree-sitter grammar for {lang} is not installed")


def test_javascript_top_level_and_class_method() -> None:
    _require("javascript")
    src = (
        "function alpha(x) { return x + 1; }\n"
        "class Beta {\n"
        "  gamma() { return 42; }\n"
        "  delta() { return 7; }\n"
        "}\n"
    )
    chunks = chunk_code(src, language="javascript")
    qualnames = sorted(c.qualified_name for c in chunks if c.qualified_name)
    # tree-sitter-javascript emits class_declaration + method_definition
    assert "alpha" in qualnames
    assert "Beta" in qualnames
    assert "Beta.gamma" in qualnames
    assert "Beta.delta" in qualnames
    by_name = {c.qualified_name: c for c in chunks}
    assert by_name["Beta.gamma"].symbol_kind == "method"
    assert by_name["Beta.gamma"].parent_qualified_name == "Beta"


def test_typescript_interface_enum_type_alias() -> None:
    _require("typescript")
    src = (
        "interface User { id: number; name: string; }\n"
        "enum Color { Red, Green, Blue }\n"
        "type Pair = { left: number; right: number };\n"
        "class Box<T> {\n"
        "  unwrap(): T { return null as unknown as T; }\n"
        "}\n"
    )
    chunks = chunk_code(src, language="typescript")
    by_name = {c.qualified_name: c for c in chunks if c.qualified_name}
    assert by_name["User"].symbol_kind == "interface"
    assert by_name["Color"].symbol_kind == "enum"
    assert by_name["Pair"].symbol_kind == "type"
    assert by_name["Box"].symbol_kind == "class"
    assert by_name["Box.unwrap"].symbol_kind == "method"


def test_go_function_method_and_type() -> None:
    _require("go")
    src = (
        "package foo\n"
        "type Counter struct { n int }\n"
        "func New() *Counter { return &Counter{} }\n"
        "func (c *Counter) Inc() { c.n += 1 }\n"
    )
    chunks = chunk_code(src, language="go")
    qualnames = {c.qualified_name for c in chunks if c.qualified_name}
    # Go emits a separate method_declaration node — qualified name
    # falls back to the bare method name (receiver-aware qualification
    # is a v1.5.0 graph-edge concern, not a chunking concern).
    assert "New" in qualnames
    assert "Inc" in qualnames
    assert "Counter" in qualnames
    by_kind = {c.qualified_name: c.symbol_kind for c in chunks if c.qualified_name}
    assert by_kind["Counter"] == "type"
    assert by_kind["Inc"] == "method"


def test_rust_struct_enum_trait_impl() -> None:
    _require("rust")
    src = (
        "struct Point { x: f64, y: f64 }\n"
        "enum Shape { Circle(f64), Square(f64) }\n"
        "trait Draw { fn draw(&self); }\n"
        "impl Draw for Point {\n"
        "    fn draw(&self) {}\n"
        "}\n"
        "fn main() {}\n"
    )
    chunks = chunk_code(src, language="rust")
    qualnames = {c.qualified_name for c in chunks if c.qualified_name}
    assert "Point" in qualnames
    assert "Shape" in qualnames
    assert "Draw" in qualnames
    assert "main" in qualnames
    by_kind = {c.qualified_name: c.symbol_kind for c in chunks if c.qualified_name}
    assert by_kind["Point"] == "struct"
    assert by_kind["Shape"] == "enum"
    assert by_kind["Draw"] == "interface"  # trait → interface kind


def test_java_class_method_interface() -> None:
    _require("java")
    src = (
        "interface Printable { void print(); }\n"
        "class Greeter implements Printable {\n"
        '  public void print() { System.out.println("hi"); }\n'
        "  public int square(int x) { return x * x; }\n"
        "}\n"
    )
    chunks = chunk_code(src, language="java")
    by_name = {c.qualified_name: c for c in chunks if c.qualified_name}
    assert by_name["Printable"].symbol_kind == "interface"
    assert by_name["Greeter"].symbol_kind == "class"
    assert by_name["Greeter.print"].symbol_kind == "method"
    assert by_name["Greeter.square"].symbol_kind == "method"


def test_cpp_namespace_class_struct_separator() -> None:
    _require("cpp")
    src = (
        "struct Point { double x; double y; };\n"
        "class Calculator {\n"
        "public:\n"
        "  int add(int a, int b) { return a + b; }\n"
        "};\n"
        "int free_function() { return 0; }\n"
    )
    chunks = chunk_code(src, language="cpp")
    qualnames = {c.qualified_name for c in chunks if c.qualified_name}
    assert "Point" in qualnames
    assert "Calculator" in qualnames
    # C++ uses :: as separator
    assert "Calculator::add" in qualnames
    assert "free_function" in qualnames
    by_kind = {c.qualified_name: c.symbol_kind for c in chunks if c.qualified_name}
    assert by_kind["Point"] == "struct"
    assert by_kind["Calculator::add"] == "method"


def test_csharp_class_struct_interface() -> None:
    _require("csharp")
    src = (
        "interface IShape { double Area(); }\n"
        "struct Vec { public double x; public double y; }\n"
        "class Circle : IShape {\n"
        "  private double r;\n"
        "  public double Area() { return 3.14 * r * r; }\n"
        "}\n"
    )
    chunks = chunk_code(src, language="csharp")
    by_name = {c.qualified_name: c for c in chunks if c.qualified_name}
    assert by_name["IShape"].symbol_kind == "interface"
    assert by_name["Vec"].symbol_kind == "struct"
    assert by_name["Circle"].symbol_kind == "class"
    assert by_name["Circle.Area"].symbol_kind == "method"


def test_unsupported_language_falls_back_to_token_window() -> None:
    """For a language with no tree-sitter grammar, dispatch falls back
    to the legacy token-window split — chunks have no qualified_name."""
    src = "(defun add (a b) (+ a b))"
    chunks = chunk_code(src, language="lisp")
    assert chunks
    assert all(c.qualified_name is None for c in chunks)


def test_unparseable_input_falls_back_silently() -> None:
    """When tree-sitter parses but produces no symbols, fallback runs."""
    src = "/* just a comment, no decls */\n"
    chunks = chunk_code(src, language="cpp")
    # The fallback path always emits at least the wrapping text chunk
    assert chunks
