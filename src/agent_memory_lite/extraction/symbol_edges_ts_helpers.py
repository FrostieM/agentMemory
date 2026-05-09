"""1.5.2: name-extraction helpers for tree-sitter edge walker.

Per-language quirks for "what does this call target?" — the field
name varies (``function`` for most C-family, ``name`` for Java's
``method_invocation``) and the call target may be a chained
member-expression that needs reconstruction.
"""

from __future__ import annotations

from typing import Any

# Identifier-bearing node types we walk into when looking for a
# call/import target name. Order matters only for documentation —
# the matcher checks `in` membership.
_IDENT_NODES: frozenset[str] = frozenset(
    {
        "identifier",
        "type_identifier",
        "field_identifier",
        "property_identifier",
        "shorthand_property_identifier",
        "scoped_identifier",  # java
        "qualified_identifier",  # cpp
        "namespace_identifier",  # cpp
        "package_identifier",  # go
        "name",  # generic fallback used by some grammars
    }
)


def _decode(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def call_target_name(call_node: Any, source: bytes) -> str | None:
    """Best-effort: reconstruct ``a.b.c`` for a call expression.

    Walks the call node's first identifier-bearing descendant and
    flattens member chains by reading the raw source between the
    callee start and end. Tree-sitter exposes a ``function`` /
    ``name`` field on most call-like nodes; we try those first then
    fall back to a depth-first scan.
    """
    callee = (
        call_node.child_by_field_name("function")
        or call_node.child_by_field_name("name")
        or call_node.child_by_field_name("constructor")
    )
    if callee is not None:
        return _decode(callee, source)
    queue: list[Any] = list(call_node.children)
    while queue:
        cur = queue.pop(0)
        if cur is None:
            continue
        if cur.type in _IDENT_NODES:
            return _decode(cur, source)
        queue.extend(cur.children)
    return None


def import_target_name(node: Any, source: bytes) -> list[str]:
    """Best-effort: extract every imported symbol name.

    Returns a list because one statement can import multiple names
    (``import {a, b} from 'x'``, ``from typing import Any, List``,
    ``use std::{io, fmt}``). We walk descendants and pick string
    literals (module paths) and identifiers (named imports). The
    caller doesn't get separate "module" vs "name" tagging — every
    import target lands as one edge owned by ``<module>``.
    """
    out: list[str] = []
    queue: list[Any] = list(node.children)
    while queue:
        cur = queue.pop(0)
        if cur is None:
            continue
        if cur.type in (
            "string",
            "string_literal",
            "string_fragment",
            "interpreted_string_literal",  # go
            "raw_string_literal",  # rust
            "system_lib_string",  # cpp #include <header>
        ):
            text = _decode(cur, source).strip("'\"")
            # cpp #include "x.h" / <x.h> — strip quotes / angle brackets.
            text = text.strip("<>")
            if text:
                out.append(text)
        elif cur.type in _IDENT_NODES:
            out.append(_decode(cur, source))
        else:
            queue.extend(cur.children)
    # Dedupe while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for name in out:
        if name not in seen:
            seen.add(name)
            deduped.append(name)
    return deduped
