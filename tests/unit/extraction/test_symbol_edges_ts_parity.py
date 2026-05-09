"""2.1.1: tree-sitter parity tests for extends / implements /
decorated_by across the 7 non-Python languages.

Pre-2.1.1 only Python emitted these edge kinds. After 2.1.1 every
supported language with a meaningful shape contributes:

| lang        | extends | implements | decorated_by |
|-------------|---------|------------|--------------|
| javascript  | yes     | n/a        | n/a (stage-3 decorators rare in practice) |
| typescript  | yes     | yes        | yes          |
| go          | n/a (composition) | n/a | n/a    |
| rust        | n/a (impl-only) | yes | yes      |
| java        | yes     | yes        | yes          |
| cpp         | yes     | n/a (no syntactic distinction) | yes |
| csharp      | yes     | n/a (no syntactic distinction) | yes |

Tests skip gracefully if the optional grammar isn't installed.
"""

from __future__ import annotations

import pytest

from agent_memory_lite.chunking.ts_grammar import is_supported
from agent_memory_lite.extraction.symbol_edges_ts import extract_ts_edges


def _require(lang: str) -> None:
    if not is_supported(lang):
        pytest.skip(f"tree-sitter grammar for {lang} is not installed")


def _by_type(edges: list, edge_type: str) -> set[tuple[str, str]]:
    return {(e.src_qualified_name, e.dst_qualified_name) for e in edges if e.edge_type == edge_type}


def test_javascript_extends() -> None:
    _require("javascript")
    src = "class Foo extends Bar {}\n"
    edges = extract_ts_edges(src, "javascript")
    assert ("Foo", "Bar") in _by_type(edges, "extends")


def test_typescript_extends_implements_decorator() -> None:
    _require("typescript")
    src = (
        "@Injectable()\n"
        "class Service extends Base implements IFetch, IClose {\n"
        "  @cache fetch() { return 1; }\n"
        "}\n"
    )
    edges = extract_ts_edges(src, "typescript")
    assert ("Service", "Base") in _by_type(edges, "extends")
    impls = _by_type(edges, "implements")
    assert ("Service", "IFetch") in impls
    assert ("Service", "IClose") in impls
    deco = _by_type(edges, "decorated_by")
    assert ("Service", "Injectable") in deco
    assert ("Service.fetch", "cache") in deco


def test_java_extends_implements_annotation() -> None:
    _require("java")
    src = (
        "class Greeter extends Base implements Speakable, Loggable {\n"
        "  @Override public void hi() {}\n"
        "}\n"
    )
    edges = extract_ts_edges(src, "java")
    assert ("Greeter", "Base") in _by_type(edges, "extends")
    impls = _by_type(edges, "implements")
    assert ("Greeter", "Speakable") in impls
    assert ("Greeter", "Loggable") in impls
    assert ("Greeter.hi", "Override") in _by_type(edges, "decorated_by")


def test_cpp_base_class_clause() -> None:
    _require("cpp")
    src = "class Calculator : public Base, private Helper {};\n"
    edges = extract_ts_edges(src, "cpp")
    extends = _by_type(edges, "extends")
    assert ("Calculator", "Base") in extends
    assert ("Calculator", "Helper") in extends


def test_csharp_base_list_and_attribute() -> None:
    _require("csharp")
    src = (
        '[Route("/api")]\n'
        "class Controller : ApiBase, IDisposable {\n"
        "  [HttpGet] public void Index() {}\n"
        "}\n"
    )
    edges = extract_ts_edges(src, "csharp")
    extends = _by_type(edges, "extends")
    assert ("Controller", "ApiBase") in extends
    assert ("Controller", "IDisposable") in extends
    deco = _by_type(edges, "decorated_by")
    assert ("Controller", "Route") in deco
    assert ("Controller.Index", "HttpGet") in deco


def test_rust_impl_for_and_attribute() -> None:
    _require("rust")
    src = "#[derive(Debug)]\nstruct Point { x: f64 }\nimpl Draw for Point { fn draw(&self) {} }\n"
    edges = extract_ts_edges(src, "rust")
    assert ("Point", "Draw") in _by_type(edges, "implements")
    deco = _by_type(edges, "decorated_by")
    assert ("Point", "derive") in deco


def test_no_false_extends_in_unrelated_class() -> None:
    """A class with NO heritage should not produce phantom extends."""
    _require("typescript")
    src = "class Standalone { value = 1; }\n"
    edges = extract_ts_edges(src, "typescript")
    assert _by_type(edges, "extends") == set()
    assert _by_type(edges, "implements") == set()


def test_method_decorator_not_attributed_to_class() -> None:
    """Regression: @cache on a method must NOT also emit a
    decorated_by edge owned by the enclosing class."""
    _require("typescript")
    src = "class Service {\n  @cache fetch() { return 1; }\n}\n"
    edges = extract_ts_edges(src, "typescript")
    deco = _by_type(edges, "decorated_by")
    assert ("Service", "cache") not in deco
    assert ("Service.fetch", "cache") in deco
