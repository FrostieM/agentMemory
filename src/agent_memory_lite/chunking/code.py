"""Code chunking dispatcher.

1.4.0: tree-sitter-driven symbol chunks for the top-7 supported
languages (python, javascript, typescript, go, rust, java, cpp,
csharp). Each declaration node — function, class, method, struct,
enum, interface, type alias — becomes its own chunk so a search
for ``ClassName.method`` lands precisely on the method body.

Pre-1.4.0 only Python had structural chunks (via stdlib ``ast``).
Other languages fell back to a token-window split, which made
symbol-level retrieval impossible on JS / TS / Go / Rust / Java /
C++ / C#. Tree-sitter is an OPTIONAL dependency: when its grammars
are absent (or the parse fails) we silently fall back to the existing
token-window symbol-extraction path so the service stays installable
without C extensions.

Heavy lifting lives in ``code_python.py`` (stdlib AST) and
``code_ts.py`` (tree-sitter); this module is just dispatch +
fallback.
"""

from __future__ import annotations

from agent_memory_lite.chunking.code_python import python_chunks
from agent_memory_lite.chunking.code_ts import ts_chunks
from agent_memory_lite.chunking.line_ranges import span_to_line_range
from agent_memory_lite.chunking.symbol_types import CodeChunk
from agent_memory_lite.chunking.symbols import extract_symbols
from agent_memory_lite.chunking.text import chunk_text

DEFAULT_MAX_TOKENS = 600

__all__ = ["DEFAULT_MAX_TOKENS", "CodeChunk", "chunk_code", "reassemble_line_range"]


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
                language=language,
            )
        )
    return out


def chunk_code(
    text: str, *, language: str | None = None, max_tokens: int = DEFAULT_MAX_TOKENS
) -> list[CodeChunk]:
    if not text.strip():
        return []
    lang = (language or "").lower()
    if lang == "python":
        primary = python_chunks(text)
        if primary:
            return primary
    elif lang:
        primary = ts_chunks(text, language=lang)
        if primary:
            return primary
    return _fallback_chunks(text, language=language, max_tokens=max_tokens)


def reassemble_line_range(text: str, chunk: CodeChunk) -> tuple[int, int]:
    return span_to_line_range(text, chunk.char_start, chunk.char_end)
