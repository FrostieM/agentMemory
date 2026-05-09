"""Tree-sitter symbol chunking helper, split from ``code.py``.

Returns [] when tree-sitter is unavailable, the language is not
supported, or the parse produced no symbols. Caller falls back to
the token-window path (``_fallback_chunks`` in ``code.py``).
"""

from __future__ import annotations

from agent_memory_lite.chunking.symbol_query import extract_symbols_via_ts
from agent_memory_lite.chunking.symbol_types import CodeChunk


def ts_chunks(text: str, *, language: str) -> list[CodeChunk]:
    """1.4.0: tree-sitter-driven symbol chunks for non-Python languages.

    Each declaration node — function, class, method, struct, enum,
    interface, type alias — becomes its own chunk so a search for
    ``ClassName.method`` (or ``ClassName::method`` for C++) lands
    precisely on the method body.
    """
    extracted = extract_symbols_via_ts(text, language)
    if not extracted:
        return []
    chunks: list[CodeChunk] = []
    for sym in extracted:
        body = text[sym.char_start : sym.char_end]
        if not body.strip():
            continue
        bare = sym.name
        symbols = [sym.qualified_name]
        extra: list[str] = []
        if bare and bare != sym.qualified_name:
            extra.append(bare)
        chunks.append(
            CodeChunk(
                text=body,
                char_start=sym.char_start,
                char_end=sym.char_end,
                line_start=sym.line_start,
                line_end=sym.line_end,
                symbols=symbols,
                symbol_kind=sym.kind,
                qualified_name=sym.qualified_name,
                parent_qualified_name=sym.parent_qualified_name,
                language=language,
                extra_symbols=extra,
            )
        )
    return chunks
