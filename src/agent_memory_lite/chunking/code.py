"""Code chunking.

Python code chunks by top-level `FunctionDef`/`AsyncFunctionDef`/`ClassDef`
nodes when parseable, falling back to a token-window split otherwise. Other
languages also fall back to the token window. Each chunk carries its own
symbol list so FTS hits can match by function/class name.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from agent_memory_lite.chunking.line_ranges import line_starts, span_to_line_range
from agent_memory_lite.chunking.symbols import extract_symbols
from agent_memory_lite.chunking.text import chunk_text

DEFAULT_MAX_TOKENS = 600


@dataclass(frozen=True, slots=True)
class CodeChunk:
    text: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    symbols: list[str]


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
    return CodeChunk(
        text=body,
        char_start=char_start,
        char_end=char_end,
        line_start=line_start,
        line_end=line_end,
        symbols=[qualname],
    )


def _python_chunks(text: str) -> list[CodeChunk]:
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


def _fallback_chunks(text: str, *, language: str | None, max_tokens: int) -> list[CodeChunk]:
    base = chunk_text(text, max_tokens=max_tokens)
    out: list[CodeChunk] = []
    for raw in base:
        symbols = extract_symbols(raw.text, language=language)
        out.append(
            CodeChunk(
                text=raw.text,
                char_start=raw.char_start,
                char_end=raw.char_end,
                line_start=raw.line_start,
                line_end=raw.line_end,
                symbols=symbols,
            )
        )
    return out


def chunk_code(
    text: str, *, language: str | None = None, max_tokens: int = DEFAULT_MAX_TOKENS
) -> list[CodeChunk]:
    if not text.strip():
        return []
    if (language or "").lower() == "python":
        primary = _python_chunks(text)
        if primary:
            return primary
    return _fallback_chunks(text, language=language, max_tokens=max_tokens)


def reassemble_line_range(text: str, chunk: CodeChunk) -> tuple[int, int]:
    return span_to_line_range(text, chunk.char_start, chunk.char_end)
