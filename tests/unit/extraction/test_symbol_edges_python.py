"""1.5.0: Python AST → SymbolEdge extractor unit tests."""

from __future__ import annotations

from agent_memory_lite.extraction.symbol_edges_python import extract_python_edges


def _by_type(edges: list, edge_type: str) -> list:
    return [e for e in edges if e.edge_type == edge_type]


def test_calls_inside_function() -> None:
    src = "def foo():\n    bar()\n    baz(1, 2)\n"
    edges = extract_python_edges(src)
    calls = _by_type(edges, "calls")
    targets = sorted(e.dst_qualified_name for e in calls)
    assert targets == ["bar", "baz"]
    assert all(e.src_qualified_name == "foo" for e in calls)


def test_method_calls_qualified_owner() -> None:
    src = (
        "class Beta:\n"
        "    def gamma(self):\n"
        "        self.helper()\n"
        "        OtherClass.static_method()\n"
    )
    edges = extract_python_edges(src)
    by_owner = {e.src_qualified_name for e in edges}
    # method calls owned by Beta.gamma; the OtherClass.static_method
    # is treated as a call (not instantiates) because PascalCase is
    # only checked on the LAST segment of the dotted name.
    assert "Beta.gamma" in by_owner


def test_imports_module_level() -> None:
    src = "import os\nimport os.path\nfrom typing import Any, List\n"
    edges = extract_python_edges(src)
    imports = _by_type(edges, "imports")
    targets = sorted(e.dst_qualified_name for e in imports)
    assert "os" in targets
    assert "os.path" in targets
    assert "typing.Any" in targets
    assert "typing.List" in targets
    assert all(e.src_qualified_name == "<module>" for e in imports)


def test_extends_with_simple_and_dotted_base() -> None:
    src = (
        "class Foo(Base): pass\nclass Bar(parent.Base): pass\nclass Multi(BaseA, mod.BaseB): pass\n"
    )
    edges = extract_python_edges(src)
    extends = _by_type(edges, "extends")
    pairs = sorted((e.src_qualified_name, e.dst_qualified_name) for e in extends)
    assert ("Foo", "Base") in pairs
    assert ("Bar", "parent.Base") in pairs
    assert ("Multi", "BaseA") in pairs
    assert ("Multi", "mod.BaseB") in pairs


def test_decorated_by_function_and_method() -> None:
    src = (
        "@cache\n"
        "def slow(): pass\n"
        "@app.route('/x')\n"
        "def handler(): pass\n"
        "class Svc:\n"
        "    @staticmethod\n"
        "    def helper(): pass\n"
    )
    edges = extract_python_edges(src)
    deco = _by_type(edges, "decorated_by")
    by_pair = {(e.src_qualified_name, e.dst_qualified_name) for e in deco}
    assert ("slow", "cache") in by_pair
    assert ("handler", "app.route") in by_pair
    assert ("Svc.helper", "staticmethod") in by_pair


def test_instantiates_pascal_case_call() -> None:
    """A call ``Foo()`` where Foo is PascalCase becomes 'instantiates'."""
    src = "def make():\n    return MyClass(1, 2)\n"
    edges = extract_python_edges(src)
    inst = _by_type(edges, "instantiates")
    assert any(e.src_qualified_name == "make" and e.dst_qualified_name == "MyClass" for e in inst)


def test_unparseable_returns_empty() -> None:
    assert extract_python_edges("def broken(:\n  return") == []


def test_module_level_calls_attached_to_module() -> None:
    """Calls at module top level (not inside def/class) are skipped —
    they have no clear owner. Module-level imports are owned by
    '<module>' but bare calls are dropped to avoid noisy ownership.
    """
    src = "print('hello')\nx = compute()\n"
    edges = extract_python_edges(src)
    calls = _by_type(edges, "calls")
    # Top-level expression calls don't belong to any def/class — extractor
    # walks only def/class bodies for calls.
    assert calls == []
