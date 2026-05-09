"""Per-language quirks for extracting a declaration's textual name.

Split out of ``symbol_query.py`` to keep the walker focused on
traversal. Tree-sitter grammars don't all expose a uniform ``name``
field on every declaration node — Go wraps types in ``type_spec``
and C++ buries function names inside ``function_declarator``.
"""

from __future__ import annotations

from typing import Any


def _decode(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _go_type_name(node: Any, source: bytes) -> str | None:
    """Go ``type_declaration`` wraps one or more ``type_spec`` nodes."""
    for child in node.children:
        if child.type == "type_spec":
            name_field = child.child_by_field_name("name")
            if name_field is not None:
                return _decode(name_field, source)
    return None


def _cpp_function_name(node: Any, source: bytes) -> str | None:
    """C++ function names live inside ``function_declarator`` →
    identifier or qualified_identifier. Scan declarator children.
    """
    declarator = node.child_by_field_name("declarator")
    queue: list[Any] = [declarator] if declarator is not None else list(node.children)
    while queue:
        cur = queue.pop(0)
        if cur is None:
            continue
        if cur.type in (
            "identifier",
            "field_identifier",
            "qualified_identifier",
            "operator_name",
        ):
            return _decode(cur, source)
        queue.extend(cur.children)
    return None


def node_name(node: Any, source: bytes, *, lang: str) -> str | None:
    """Extract the textual name of a declaration node. Per-language
    quirks handled inline; for everything else, try the ``name`` field
    first then fall back to walking children for identifiers.
    """
    if lang == "go" and node.type == "type_declaration":
        return _go_type_name(node, source)
    if lang == "cpp" and node.type == "function_definition":
        return _cpp_function_name(node, source)
    name_field = node.child_by_field_name("name")
    if name_field is not None:
        return _decode(name_field, source)
    for child in node.children:
        if child.type in (
            "identifier",
            "type_identifier",
            "field_identifier",
            "property_identifier",
        ):
            return _decode(child, source)
    return None
