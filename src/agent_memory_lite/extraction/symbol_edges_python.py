"""1.5.0: Python AST → SymbolEdge list.

Walks one Python source string and emits the edges that belong to
each top-level symbol or method:

* ``calls``        — every call site whose target name is resolvable
  to a string (``foo()``, ``self.bar()``, ``module.helper()``)
* ``imports``      — module-level ``import x`` and ``from x import y``
* ``extends``      — ``class Foo(Base):`` → edge from Foo to Base
* ``decorated_by`` — ``@decorator`` on a function or class
* ``instantiates`` — limited: the call ``Foo()`` where ``Foo`` looks
  like a class (PascalCase) emits an instantiates edge instead of
  a generic calls edge

Edge owner: each edge's ``src_qualified_name`` is the enclosing
function / method / class; for module-level imports the owner is
``<module>`` (a synthetic root that the resolver pass can later
attach to the file's first chunk).

Pure helper — no DB / no I/O. Returns dataclass tuples; the caller
builds ``EdgeIn`` rows with workspace_id + chunk_id resolution.
"""

from __future__ import annotations

import ast

from agent_memory_lite.extraction.symbol_edges_py_helpers import (
    ExtractedEdge,
    attr_chain,
    decorator_target,
    walk_body,
)

__all__ = ["ExtractedEdge", "extract_python_edges"]


def _emit_imports(stmt: ast.Import | ast.ImportFrom, out: list[ExtractedEdge]) -> None:
    if isinstance(stmt, ast.Import):
        for alias in stmt.names:
            out.append(
                ExtractedEdge(
                    src_qualified_name="<module>",
                    dst_qualified_name=alias.name,
                    edge_type="imports",
                )
            )
        return
    module = stmt.module or ""
    for alias in stmt.names:
        target = f"{module}.{alias.name}" if module else alias.name
        out.append(
            ExtractedEdge(
                src_qualified_name="<module>",
                dst_qualified_name=target,
                edge_type="imports",
            )
        )


def _emit_function(
    stmt: ast.FunctionDef | ast.AsyncFunctionDef,
    out: list[ExtractedEdge],
    *,
    qname: str | None = None,
) -> None:
    owner = qname or stmt.name
    for deco in stmt.decorator_list:
        target = decorator_target(deco)
        if target:
            out.append(
                ExtractedEdge(
                    src_qualified_name=owner,
                    dst_qualified_name=target,
                    edge_type="decorated_by",
                )
            )
    walk_body(stmt.body, owner, out)


def _emit_class(stmt: ast.ClassDef, out: list[ExtractedEdge]) -> None:
    for base in stmt.bases:
        if isinstance(base, ast.Attribute | ast.Name):
            target = attr_chain(base)
            if target:
                out.append(
                    ExtractedEdge(
                        src_qualified_name=stmt.name,
                        dst_qualified_name=target,
                        edge_type="extends",
                    )
                )
    for deco in stmt.decorator_list:
        target = decorator_target(deco)
        if target:
            out.append(
                ExtractedEdge(
                    src_qualified_name=stmt.name,
                    dst_qualified_name=target,
                    edge_type="decorated_by",
                )
            )
    for child in stmt.body:
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            _emit_function(child, out, qname=f"{stmt.name}.{child.name}")


def extract_python_edges(text: str) -> list[ExtractedEdge]:
    """Return one flat list of edges for the whole module body."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    edges: list[ExtractedEdge] = []
    for stmt in tree.body:
        if isinstance(stmt, ast.Import | ast.ImportFrom):
            _emit_imports(stmt, edges)
        elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            _emit_function(stmt, edges)
        elif isinstance(stmt, ast.ClassDef):
            _emit_class(stmt, edges)
    return edges
