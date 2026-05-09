"""1.5.0: helpers for the Python edge extractor.

Split from ``symbol_edges_python.py`` so the public entry point
``extract_python_edges`` stays under the SLOC ceiling. These helpers
recover qualified names from ast nodes and walk a function / method
body collecting call sites.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExtractedEdge:
    src_qualified_name: str  # 'paperBot.calculate', '<module>'
    dst_qualified_name: str
    edge_type: str  # 'calls' | 'imports' | 'extends' | 'decorated_by' | 'instantiates'


def attr_chain(node: ast.AST) -> str | None:
    """Reconstruct ``a.b.c`` from a chain of ast.Attribute / ast.Name."""
    parts: list[str] = []
    cur: ast.AST | None = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    elif cur is not None:
        return None
    parts.reverse()
    return ".".join(parts)


def call_target(call: ast.Call) -> str | None:
    """Best-effort: a call's target qualified name."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return attr_chain(func)
    return None


def decorator_target(deco: ast.AST) -> str | None:
    """``@foo``, ``@foo.bar``, ``@foo(arg)`` → 'foo' / 'foo.bar' / 'foo'."""
    if isinstance(deco, ast.Call):
        return call_target(deco)
    if isinstance(deco, ast.Name):
        return deco.id
    if isinstance(deco, ast.Attribute):
        return attr_chain(deco)
    return None


def is_classlike(name: str) -> bool:
    """Heuristic: a bare name in PascalCase looks like a class."""
    return bool(name) and name[0].isupper() and "_" not in name.split(".", maxsplit=1)[0]


def walk_body(body: list[ast.stmt], owner: str, out: list[ExtractedEdge]) -> None:
    """Collect every call site in ``body`` as a calls / instantiates edge."""
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                target = call_target(node)
                if not target:
                    continue
                last = target.rsplit(".", 1)[-1]
                edge_type = "instantiates" if is_classlike(last) else "calls"
                out.append(
                    ExtractedEdge(
                        src_qualified_name=owner,
                        dst_qualified_name=target,
                        edge_type=edge_type,
                    )
                )
