"""Python-AST symbol chunking helper, split from ``code.py``.

Python is special: stdlib ``ast`` is zero-dep and faster than tree-sitter
for the common case, so we keep a dedicated path for it. See
``code_ts.py`` for the tree-sitter dispatch used by every other
supported language.
"""

from __future__ import annotations

import ast

from agent_memory_lite.chunking.line_ranges import line_starts
from agent_memory_lite.chunking.symbol_types import CodeChunk


def _node_to_chunk(
    node: ast.AST,
    *,
    text: str,
    starts: list[int],
    qualname: str,
) -> CodeChunk | None:
    """Slice a Python AST node into a CodeChunk with qualified-name symbol."""
    line_start = getattr(node, "lineno", 0)
    line_end = getattr(node, "end_lineno", line_start) or line_start
    if line_start <= 0 or line_end <= 0:
        return None
    char_start = starts[line_start - 1] if line_start - 1 < len(starts) else 0
    if line_end < len(starts):
        char_end = starts[line_end] if line_end < len(starts) else len(text)
    else:
        char_end = len(text)
    body = text[char_start:char_end]
    if not body.strip():
        return None
    bare_name = qualname.rsplit(".", 1)[-1]
    is_method = "." in qualname
    is_class = isinstance(node, ast.ClassDef)
    if is_class:
        kind = "class"
    elif is_method:
        kind = "method"
    else:
        kind = "function"
    return CodeChunk(
        text=body,
        char_start=char_start,
        char_end=char_end,
        line_start=line_start,
        line_end=line_end,
        symbols=[qualname],
        symbol_kind=kind,
        qualified_name=qualname,
        parent_qualified_name=qualname.rsplit(".", 1)[0] if is_method else None,
        language="python",
        extra_symbols=[bare_name] if is_method and bare_name != qualname else [],
    )


def python_chunks(text: str) -> list[CodeChunk]:
    """Top-level functions, classes, AND methods inside classes.

    1.3.0: methods get their own chunk with ``symbols=["Class.method"]``
    so a search for ``paperBot.calculate`` lands precisely on the method
    body, not on the entire class. Top-level functions/classes still
    keep their bare name as a symbol. Class chunk and method chunks
    overlap textually — that's intentional: searching by class name OR
    by method name both surface the right span.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    starts = line_starts(text)
    chunks: list[CodeChunk] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            chunk = _node_to_chunk(node, text=text, starts=starts, qualname=node.name)
            if chunk is not None:
                chunks.append(chunk)
        elif isinstance(node, ast.ClassDef):
            class_chunk = _node_to_chunk(node, text=text, starts=starts, qualname=node.name)
            if class_chunk is not None:
                chunks.append(class_chunk)
            # Method-level chunks for FunctionDef / AsyncFunctionDef
            # children of the class body. Skip nested classes inside
            # classes for now — uncommon and keeps the chunk count
            # bounded.
            for child in node.body:
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    method_chunk = _node_to_chunk(
                        child,
                        text=text,
                        starts=starts,
                        qualname=f"{node.name}.{child.name}",
                    )
                    if method_chunk is not None:
                        chunks.append(method_chunk)
    return chunks
