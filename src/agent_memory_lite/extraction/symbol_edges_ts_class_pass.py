"""2.1.1: tree-sitter walker pass that emits class-heritage +
decorator edges. Split from ``symbol_edges_ts.py`` so the main
walker stays under the SLOC ceiling.

Three edge kinds:
* ``extends`` / ``implements`` — class-heritage children of
  language-specific class-like nodes (per CLASSLIKE_NODES).
* ``implements`` — Rust ``impl Trait for Type`` shape.
* ``decorated_by`` — decorator / annotation / attribute siblings or
  children of any declaration.
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.chunking.symbol_decls import LANG_DECLS
from agent_memory_lite.chunking.symbol_query import ExtractedSymbol
from agent_memory_lite.extraction.symbol_edges_py_helpers import ExtractedEdge
from agent_memory_lite.extraction.symbol_edges_ts_decls import (
    CLASSLIKE_NODES,
    DECORATOR_NODES,
)
from agent_memory_lite.extraction.symbol_edges_ts_decorators import decorators_for
from agent_memory_lite.extraction.symbol_edges_ts_heritage import (
    class_heritage,
    rust_impl_target,
)


def _enclosing_owner(symbols: list[ExtractedSymbol], offset: int) -> str | None:
    best: ExtractedSymbol | None = None
    best_size: int | None = None
    for sym in symbols:
        if sym.char_start <= offset < sym.char_end:
            size = sym.char_end - sym.char_start
            if best is None or size < (best_size or 0):
                best = sym
                best_size = size
    return best.qualified_name if best is not None else None


def _node_name(node: Any, source: bytes) -> str | None:
    name_field = node.child_by_field_name("name")
    if name_field is not None:
        return source[name_field.start_byte : name_field.end_byte].decode("utf-8", errors="replace")
    for child in node.children:
        if child.type in ("identifier", "type_identifier"):
            return source[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
    return None


def _emit(
    out: list[ExtractedEdge],
    seen: set[tuple[str, str, str]],
    *,
    src: str,
    dst: str,
    kind: str,
) -> None:
    key = (src, dst, kind)
    if key in seen:
        return
    seen.add(key)
    out.append(ExtractedEdge(src_qualified_name=src, dst_qualified_name=dst, edge_type=kind))


def _emit_heritage(
    cur: Any,
    *,
    source: bytes,
    lang: str,
    symbols: list[ExtractedSymbol],
    out: list[ExtractedEdge],
    seen: set[tuple[str, str, str]],
) -> None:
    cls_name = _enclosing_owner(symbols, cur.start_byte) or _node_name(cur, source)
    if not cls_name:
        return
    for target, kind in class_heritage(cur, source, lang):
        _emit(out, seen, src=cls_name, dst=target, kind=kind)


def _emit_rust_impl(
    cur: Any,
    *,
    source: bytes,
    out: list[ExtractedEdge],
    seen: set[tuple[str, str, str]],
) -> None:
    pair = rust_impl_target(cur, source)
    if pair is None:
        return
    target_type, trait = pair
    _emit(out, seen, src=target_type, dst=trait, kind="implements")


def _emit_decorators(
    cur: Any,
    parent: Any,
    *,
    source: bytes,
    lang: str,
    symbols: list[ExtractedSymbol],
    out: list[ExtractedEdge],
    seen: set[tuple[str, str, str]],
) -> None:
    owner = _enclosing_owner(symbols, cur.start_byte) or _node_name(cur, source)
    if owner is None:
        return
    for deco in decorators_for(cur, parent, source, lang):
        _emit(out, seen, src=owner, dst=deco, kind="decorated_by")


def collect_heritage_and_decorators(
    root: Any,
    *,
    source: bytes,
    lang: str,
    symbols: list[ExtractedSymbol],
    out: list[ExtractedEdge],
) -> None:
    classlike = CLASSLIKE_NODES.get(lang, frozenset())
    decoratable_lang = DECORATOR_NODES.get(lang, frozenset())
    decl_types = frozenset(LANG_DECLS.get(lang, {}).keys()) | classlike
    seen: set[tuple[str, str, str]] = set()
    queue: list[tuple[Any, Any]] = [(root, None)]
    while queue:
        cur, parent = queue.pop(0)
        if cur.type in classlike:
            _emit_heritage(cur, source=source, lang=lang, symbols=symbols, out=out, seen=seen)
        if lang == "rust" and cur.type == "impl_item":
            _emit_rust_impl(cur, source=source, out=out, seen=seen)
        if decoratable_lang and cur.type in decl_types:
            _emit_decorators(
                cur,
                parent,
                source=source,
                lang=lang,
                symbols=symbols,
                out=out,
                seen=seen,
            )
        for child in cur.children:
            queue.append((child, cur))
