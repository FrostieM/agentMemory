"""1.5.2: tree-sitter-driven SymbolEdge extractor for the top-7
non-Python languages (JavaScript, TypeScript, Go, Rust, Java, C++, C#).

Architecture: re-uses ``extract_symbols_via_ts`` from the chunking
layer to figure out which byte ranges each function / method / class
covers, then walks the tree-sitter tree once more, this time looking
for ``call_expression`` (or language-equivalent) and import
statements. For every call site, the walker maps its byte position
to the smallest enclosing symbol and emits a ``calls`` /
``instantiates`` edge owned by that symbol's qualified name.

Returns the same ``ExtractedEdge`` shape as the Python AST extractor
(``extraction/symbol_edges_python.py``), so the file ingest pipeline
can dispatch by language and accumulate edges uniformly.
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.chunking.symbol_query import (
    ExtractedSymbol,
    extract_symbols_via_ts,
)
from agent_memory_lite.chunking.ts_grammar import get_parser
from agent_memory_lite.extraction.symbol_edges_py_helpers import ExtractedEdge
from agent_memory_lite.extraction.symbol_edges_ts_class_pass import (
    collect_heritage_and_decorators,
)
from agent_memory_lite.extraction.symbol_edges_ts_decls import (
    CALL_NODES,
    IMPORT_NODES,
    INSTANTIATES_NODES,
)
from agent_memory_lite.extraction.symbol_edges_ts_helpers import (
    call_target_name,
    import_target_name,
)


def _enclosing_owner(symbols: list[ExtractedSymbol], offset: int) -> str | None:
    """Smallest symbol whose byte range contains ``offset``.

    Function / method bodies nest inside class bodies, so picking
    the smallest match yields the right owner (the class chunk
    contains the method chunk; a call inside the method should be
    owned by the method, not the class).
    """
    best: ExtractedSymbol | None = None
    best_size: int | None = None
    for sym in symbols:
        if sym.char_start <= offset < sym.char_end:
            size = sym.char_end - sym.char_start
            if best is None or size < (best_size or 0):
                best = sym
                best_size = size
    return best.qualified_name if best is not None else None


def _collect_calls(
    root: Any,
    *,
    source: bytes,
    lang: str,
    symbols: list[ExtractedSymbol],
    out: list[ExtractedEdge],
) -> None:
    call_types = CALL_NODES.get(lang, frozenset())
    if not call_types:
        return
    queue: list[Any] = list(root.children)
    while queue:
        cur = queue.pop(0)
        if cur.type in call_types:
            owner = _enclosing_owner(symbols, cur.start_byte)
            if owner is not None:
                target = call_target_name(cur, source)
                if target:
                    edge_type = "instantiates" if cur.type in INSTANTIATES_NODES else "calls"
                    out.append(
                        ExtractedEdge(
                            src_qualified_name=owner,
                            dst_qualified_name=target,
                            edge_type=edge_type,
                        )
                    )
        queue.extend(cur.children)


def _collect_imports(root: Any, *, source: bytes, lang: str, out: list[ExtractedEdge]) -> None:
    import_types = IMPORT_NODES.get(lang, frozenset())
    if not import_types:
        return
    for child in root.children:
        if child.type in import_types:
            for target in import_target_name(child, source):
                out.append(
                    ExtractedEdge(
                        src_qualified_name="<module>",
                        dst_qualified_name=target,
                        edge_type="imports",
                    )
                )


def extract_ts_edges(text: str, lang: str) -> list[ExtractedEdge]:
    """Tree-sitter-driven edge extraction. Returns [] when the
    language is unsupported or the parser is unavailable.
    """
    parser = get_parser(lang)
    if parser is None:
        return []
    source = text.encode("utf-8", errors="replace")
    try:
        tree = parser.parse(source)
    except Exception:
        return []
    symbols = extract_symbols_via_ts(text, lang)
    out: list[ExtractedEdge] = []
    _collect_imports(tree.root_node, source=source, lang=lang, out=out)
    _collect_calls(tree.root_node, source=source, lang=lang, symbols=symbols, out=out)
    collect_heritage_and_decorators(
        tree.root_node, source=source, lang=lang, symbols=symbols, out=out
    )
    return out
