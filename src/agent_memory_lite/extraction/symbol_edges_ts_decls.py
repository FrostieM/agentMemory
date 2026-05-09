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
