"""1.5.2: per-language node-type tables for tree-sitter edge extraction.

Each language entry captures the minimum nodes needed to emit
``calls`` and ``imports`` edges. Adding ``extends`` /
``decorated_by`` / ``instantiates`` is a v1.5.3+ extension; the
schema and pipeline already accept those types.

Tree-sitter node names vary across grammars — Java uses
``method_invocation`` while everyone else uses ``call_expression``;
C# uses ``invocation_expression``; C++ imports are
``preproc_include`` (pre-processor directive). The tables below
codify those quirks so the walker stays generic.
"""

from __future__ import annotations

# Call-expression node types per language. The walker emits one
# ``calls`` edge per match within a function / method body.
CALL_NODES: dict[str, frozenset[str]] = {
    "javascript": frozenset({"call_expression", "new_expression"}),
    "typescript": frozenset({"call_expression", "new_expression"}),
    "go": frozenset({"call_expression"}),
    "rust": frozenset({"call_expression", "macro_invocation"}),
    "java": frozenset({"method_invocation", "object_creation_expression"}),
    "cpp": frozenset({"call_expression", "new_expression"}),
    "csharp": frozenset({"invocation_expression", "object_creation_expression"}),
}

# Import / use statement node types per language. The walker emits
# one ``imports`` edge per matched name.
IMPORT_NODES: dict[str, frozenset[str]] = {
    "javascript": frozenset({"import_statement"}),
    "typescript": frozenset({"import_statement"}),
    "go": frozenset({"import_declaration"}),
    "rust": frozenset({"use_declaration"}),
    "java": frozenset({"import_declaration"}),
    "cpp": frozenset({"preproc_include"}),
    "csharp": frozenset({"using_directive"}),
}

# Languages whose ``new_expression`` / ``object_creation_expression``
# should produce ``instantiates`` instead of ``calls``. C++'s
# ``new_expression`` is a heap allocation; we treat it the same as
# Java / C# constructor calls — explicit instantiation.
INSTANTIATES_NODES: frozenset[str] = frozenset({"new_expression", "object_creation_expression"})

# 2.1.1: class-like declaration node types whose heritage we walk
# for extends / implements edges. Per-language because grammar
# names diverge (class_specifier vs class_declaration etc.).
CLASSLIKE_NODES: dict[str, frozenset[str]] = {
    "javascript": frozenset({"class_declaration"}),
    "typescript": frozenset({"class_declaration"}),
    "java": frozenset({"class_declaration", "interface_declaration"}),
    "cpp": frozenset({"class_specifier", "struct_specifier"}),
    "csharp": frozenset({"class_declaration", "struct_declaration"}),
    # rust: handled separately via impl_item — see ts_heritage.
}

# 2.1.1: decorator / annotation / attribute node types per language.
# When one of these precedes or wraps a declaration, the walker
# emits a ``decorated_by`` edge from the declaration to the
# decorator's first identifier.
DECORATOR_NODES: dict[str, frozenset[str]] = {
    "typescript": frozenset({"decorator"}),
    "javascript": frozenset({"decorator"}),  # tc39 stage-3
    "java": frozenset({"annotation", "marker_annotation"}),
    "cpp": frozenset({"attribute_declaration", "attribute_specifier"}),
    "csharp": frozenset({"attribute_list"}),
    "rust": frozenset({"attribute_item"}),
    # go: no native annotation syntax — intentionally absent.
}
