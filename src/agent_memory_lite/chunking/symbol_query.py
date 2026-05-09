"""1.4.0: tree-sitter symbol-extraction walker for the top-7 languages.

Walks a tree-sitter AST and emits one ``ExtractedSymbol`` per
declaration node — function, class, method, struct, enum, interface,
type alias. Container nodes (class / interface / struct / enum)
recurse into their body so methods get qualified names like
``Class.method`` (or ``Class::method`` for C++).

The per-language declaration tables live in ``symbol_decls.py``;
the per-language name-extraction quirks live in ``symbol_naming.py``;
this module is just the walker + entry point.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_memory_lite.chunking.symbol_decls import (
    CONTAINER_KINDS,
    LANG_DECLS,
    TRANSPARENT_NODES,
)
from agent_memory_lite.chunking.symbol_naming import node_name


@dataclass(frozen=True, slots=True)
class ExtractedSymbol:
    name: str
    qualified_name: str
    kind: str  # 'function' | 'class' | 'method' | 'struct' | 'interface' | 'enum' | 'type'
    parent_qualified_name: str | None
    line_start: int
    line_end: int
    char_start: int
    char_end: int


def _walk(
    node: Any,
    *,
    source: bytes,
    lang: str,
    parent_qname: str | None,
    out: list[ExtractedSymbol],
) -> None:
    decls = LANG_DECLS.get(lang, {})
    for child in node.children:
        kind = decls.get(child.type)
        if kind is None:
            # Recurse into namespaces / impl blocks AND every other
            # non-empty wrapper without naming them as symbols
            # themselves. TRANSPARENT_NODES is documentation: it is
            # what we *intend* to recurse through, but we recurse into
            # any non-leaf node anyway to catch nested decls.
            if child.type in TRANSPARENT_NODES or child.child_count > 0:
                _walk(child, source=source, lang=lang, parent_qname=parent_qname, out=out)
            continue
        name = node_name(child, source, lang=lang)
        if name is None:
            continue
        sep = "::" if lang == "cpp" else "."
        qname = f"{parent_qname}{sep}{name}" if parent_qname else name
        out.append(
            ExtractedSymbol(
                name=name,
                qualified_name=qname,
                kind="method" if (parent_qname and kind == "function") else kind,
                parent_qualified_name=parent_qname,
                line_start=child.start_point[0] + 1,
                line_end=child.end_point[0] + 1,
                char_start=child.start_byte,
                char_end=child.end_byte,
            )
        )
        # Containers (class / interface / struct / enum) recurse into
        # their body so methods get qualified names.
        if kind in CONTAINER_KINDS or kind == "class":
            _walk(child, source=source, lang=lang, parent_qname=qname, out=out)


def extract_symbols_via_ts(text: str, lang: str) -> list[ExtractedSymbol]:
    """Tree-sitter-driven symbol extraction. Returns [] when the
    language is unsupported or the parser is unavailable. Never
    raises — caller must handle empty list as "fall back to text
    chunking".
    """
    from agent_memory_lite.chunking.ts_grammar import get_parser  # noqa: PLC0415

    parser = get_parser(lang)
    if parser is None:
        return []
    source = text.encode("utf-8", errors="replace")
    try:
        tree = parser.parse(source)
    except Exception:
        return []
    out: list[ExtractedSymbol] = []
    _walk(tree.root_node, source=source, lang=lang, parent_qname=None, out=out)
    return out
