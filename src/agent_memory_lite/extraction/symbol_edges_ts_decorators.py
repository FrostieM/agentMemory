"""2.1.1: per-language decorator / annotation / attribute extraction.

Extracts the decorator's first identifier from grammar-specific
shapes. Two patterns across languages:

* CHILD pattern: TypeScript/JavaScript wrap ``decorator`` as a
  direct child of ``class_declaration`` / ``method_definition``.
  C# does the same with ``attribute_list``. Java wraps in a
  ``modifiers`` field whose children include ``annotation`` /
  ``marker_annotation``.

* PRECEDING-SIBLING pattern: Rust ``attribute_item`` precedes
  ``struct_item`` / ``function_item`` as a sibling. C++
  ``attribute_declaration`` precedes ``declaration``.

We handle both. The walker calls ``decorators_for(decl_node, parent,
source, lang)`` with both the declaration node AND its parent so it
can scan siblings.
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.extraction.symbol_edges_ts_decls import DECORATOR_NODES
from agent_memory_lite.extraction.symbol_edges_ts_helpers import _decode

_IDENT_TYPES: frozenset[str] = frozenset(
    {"identifier", "type_identifier", "scoped_identifier", "property_identifier"}
)


def _first_ident(node: Any, source: bytes) -> str | None:
    """First identifier-bearing descendant, depth-first."""
    queue: list[Any] = list(node.children)
    while queue:
        cur = queue.pop(0)
        if cur is None:
            continue
        if cur.type in _IDENT_TYPES:
            return _decode(cur, source)
        queue.extend(cur.children)
    return None


def _scan_children(decl: Any, source: bytes, decorator_types: frozenset[str]) -> list[str]:
    """Find decorators that are direct children OR live inside a
    ``modifiers`` wrapper (Java pattern)."""
    out: list[str] = []
    for child in decl.children:
        if child.type in decorator_types:
            name = _first_ident(child, source)
            if name:
                out.append(name)
        elif child.type == "modifiers":
            for sub in child.children:
                if sub.type in decorator_types:
                    name = _first_ident(sub, source)
                    if name:
                        out.append(name)
    return out


def _scan_preceding_siblings(
    decl: Any, parent: Any, source: bytes, decorator_types: frozenset[str]
) -> list[str]:
    """Walk parent.children; collect contiguous decorator/attribute
    siblings that immediately precede ``decl`` (Rust + C++ pattern).
    """
    out: list[str] = []
    if parent is None:
        return out
    siblings = list(parent.children)
    idx = -1
    for i, sib in enumerate(siblings):
        if sib.start_byte == decl.start_byte and sib.end_byte == decl.end_byte:
            idx = i
            break
    if idx < 0:
        return out
    cursor = idx - 1
    while cursor >= 0:
        sib = siblings[cursor]
        if sib.type in decorator_types:
            name = _first_ident(sib, source)
            if name:
                out.append(name)
            cursor -= 1
        elif sib.type in ("comment", "line_comment", "block_comment"):
            cursor -= 1  # skip comments between attribute and decl
        else:
            break
    out.reverse()
    return out


def decorators_for(decl: Any, parent: Any, source: bytes, lang: str) -> list[str]:
    """Return every decorator/annotation name attached to ``decl``."""
    decorator_types = DECORATOR_NODES.get(lang, frozenset())
    if not decorator_types:
        return []
    found = _scan_children(decl, source, decorator_types)
    found.extend(_scan_preceding_siblings(decl, parent, source, decorator_types))
    seen: set[str] = set()
    deduped: list[str] = []
    for name in found:
        if name not in seen:
            seen.add(name)
            deduped.append(name)
    return deduped
