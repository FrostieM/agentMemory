"""Per-language declaration node tables for the tree-sitter walker.

Split out of ``symbol_query.py`` so the walker module stays under
the SLOC ceiling. CONTAINER nodes have child declarations that
should be qualified with the container's name (e.g. methods inside
a class get ``Class.method``).
"""

from __future__ import annotations

# Per-language declaration node types and the symbol kinds they produce.
LANG_DECLS: dict[str, dict[str, str]] = {
    "python": {
        "function_definition": "function",
        "class_definition": "class",
    },
    "javascript": {
        "function_declaration": "function",
        "method_definition": "method",
        "class_declaration": "class",
    },
    "typescript": {
        "function_declaration": "function",
        "method_definition": "method",
        "class_declaration": "class",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "enum_declaration": "enum",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_declaration": "type",
    },
    "rust": {
        "function_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "trait_item": "interface",
        # impl_item is intentionally NOT a top-level chunk — we
        # recurse into it for function_item children but don't emit
        # the impl block itself (it has no clean name; tree-sitter
        # exposes both the trait and the type as separate fields).
    },
    "java": {
        "method_declaration": "method",
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
    },
    "cpp": {
        "function_definition": "function",
        "class_specifier": "class",
        "struct_specifier": "struct",
        "enum_specifier": "enum",
    },
    "csharp": {
        "method_declaration": "method",
        "class_declaration": "class",
        "interface_declaration": "interface",
        "struct_declaration": "struct",
        "enum_declaration": "enum",
    },
}

CONTAINER_KINDS: frozenset[str] = frozenset({"class", "interface", "struct", "enum"})

# Languages that recurse into impl-like wrappers without emitting a chunk.
TRANSPARENT_NODES: frozenset[str] = frozenset(
    {
        "namespace_definition",
        "namespace_declaration",
        "module",
        "impl_item",  # rust
        "linkage_specification",  # cpp `extern "C" { ... }`
    }
)
