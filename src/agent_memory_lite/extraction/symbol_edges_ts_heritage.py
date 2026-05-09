"""2.1.1: per-language class-heritage extraction.

Maps a class-like declaration node to a list of (target_name,
edge_kind) pairs. Tree-sitter grammar names diverge sharply across
languages (TypeScript wraps everything in ``class_heritage``; Java
uses two separate sibling fields ``superclass`` + ``super_interfaces``;
C++ flattens everything into ``base_class_clause`` with no
extends/implements distinction; Rust models heritage through
``impl_item`` which has no class-like enclosing node).

Returns a list of (target, kind) tuples — kind is ``extends``,
``implements``, or in C++/C# ``extends`` for everything (the syntax
doesn't distinguish, callers do).
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.extraction.symbol_edges_ts_helpers import _decode

_IDENT_TYPES: frozenset[str] = frozenset(
    {"identifier", "type_identifier", "scoped_identifier", "qualified_identifier"}
)


def _ident_in(node: Any, source: bytes) -> str | None:
    for child in node.children:
        if child.type in _IDENT_TYPES:
            return _decode(child, source)
    return None


def _all_idents(node: Any, source: bytes) -> list[str]:
    return [_decode(c, source) for c in node.children if c.type in _IDENT_TYPES]


def _ts_js_heritage(class_node: Any, source: bytes) -> list[tuple[str, str]]:
    """TypeScript: class_heritage → extends_clause / implements_clause.
    JavaScript: class_heritage → bare ``extends`` keyword + identifier
    siblings, no clause wrapper. Handle both shapes.
    """
    out: list[tuple[str, str]] = []
    for child in class_node.children:
        if child.type != "class_heritage":
            continue
        clausey = [c for c in child.children if c.type in ("extends_clause", "implements_clause")]
        for clause in clausey:
            if clause.type == "extends_clause":
                name = _ident_in(clause, source)
                if name:
                    out.append((name, "extends"))
            elif clause.type == "implements_clause":
                for name in _all_idents(clause, source):
                    out.append((name, "implements"))
        if not clausey:
            # JavaScript flat shape: <extends> <identifier>
            seen = False
            for sub in child.children:
                if sub.type == "extends":
                    seen = True
                elif seen and sub.type in _IDENT_TYPES:
                    out.append((_decode(sub, source), "extends"))
                    break
    return out


def _java_heritage(class_node: Any, source: bytes) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for child in class_node.children:
        if child.type == "superclass":
            name = _ident_in(child, source)
            if name:
                out.append((name, "extends"))
        elif child.type == "super_interfaces":
            for sub in child.children:
                if sub.type == "type_list":
                    for name in _all_idents(sub, source):
                        out.append((name, "implements"))
        elif child.type == "extends_interfaces":
            # interface extends interface — interface_declaration shape
            for sub in child.children:
                if sub.type == "type_list":
                    for name in _all_idents(sub, source):
                        out.append((name, "extends"))
    return out


def _cpp_heritage(class_node: Any, source: bytes) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for child in class_node.children:
        if child.type != "base_class_clause":
            continue
        for name in _all_idents(child, source):
            out.append((name, "extends"))
    return out


def _csharp_heritage(class_node: Any, source: bytes) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for child in class_node.children:
        if child.type != "base_list":
            continue
        for name in _all_idents(child, source):
            out.append((name, "extends"))
    return out


def class_heritage(class_node: Any, source: bytes, lang: str) -> list[tuple[str, str]]:
    """Dispatch on language; return [] when language has no shape."""
    if lang in ("typescript", "javascript"):
        return _ts_js_heritage(class_node, source)
    if lang == "java":
        return _java_heritage(class_node, source)
    if lang == "cpp":
        return _cpp_heritage(class_node, source)
    if lang == "csharp":
        return _csharp_heritage(class_node, source)
    return []


def rust_impl_target(impl_node: Any, source: bytes) -> tuple[str, str] | None:
    """Rust ``impl Trait for Type`` → returns (target_type, trait_name)
    pair; the caller emits an ``implements`` edge from target to trait.

    Plain ``impl Type {}`` (no trait) → returns None.
    """
    types: list[str] = []
    has_for = False
    for child in impl_node.children:
        if child.type == "type_identifier":
            types.append(_decode(child, source))
        elif child.type == "for":
            has_for = True
    if has_for and len(types) >= 2:
        # First type is the trait, second is the type implementing it.
        trait, target_type = types[0], types[1]
        return target_type, trait
    return None
