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


def _python_chunks(text: str) -> list[CodeChunk]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    starts = line_starts(text)
    chunks: list[CodeChunk] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        line_start = node.lineno
        line_end = getattr(node, "end_lineno", node.lineno) or node.lineno
        if line_start <= 0 or line_end <= 0:
            continue
        char_start = starts[line_start - 1] if line_start - 1 < len(starts) else 0
        if line_end < len(starts):
            char_end = starts[line_end] if line_end < len(starts) else len(text)
        else:
            char_end = len(text)
        body = text[char_start:char_end]
        if not body.strip():
            continue
        chunks.append(
            CodeChunk(
                text=body,
                char_start=char_start,
                char_end=char_end,
                line_start=line_start,
                line_end=line_end,
                symbols=[node.name],
            )
        )
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
