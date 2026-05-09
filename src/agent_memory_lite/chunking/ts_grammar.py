"""1.4.0: tree-sitter parser loader for the top-7 supported languages.

Tree-sitter is an OPTIONAL dependency. When the ``tree-sitter`` package
or any specific grammar package is missing, ``get_parser(lang)`` returns
``None`` and callers fall back to the existing token-window / Python AST
chunking. The service stays installable without C extensions.

Supported languages (mapped to grammar package names):
    python      → tree_sitter_python
    javascript  → tree_sitter_javascript
    typescript  → tree_sitter_typescript (language `typescript` and `tsx`)
    go          → tree_sitter_go
    rust        → tree_sitter_rust
    java        → tree_sitter_java
    cpp         → tree_sitter_cpp
    csharp      → tree_sitter_c_sharp

Loader is cached: each grammar parser is constructed once per process.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

# Logical language names used by the rest of the codebase.
SUPPORTED_LANGS: tuple[str, ...] = (
    "python",
    "javascript",
    "typescript",
    "go",
    "rust",
    "java",
    "cpp",
    "csharp",
)

# Map logical language → (grammar module, attribute name).
# tree-sitter-typescript exposes both `language_typescript` and
# `language_tsx`; we wire `typescript` here and treat `.tsx` files as
# the same language at the dispatch level.
_GRAMMAR_TABLE: dict[str, tuple[str, str]] = {
    "python": ("tree_sitter_python", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "go": ("tree_sitter_go", "language"),
    "rust": ("tree_sitter_rust", "language"),
    "java": ("tree_sitter_java", "language"),
    "cpp": ("tree_sitter_cpp", "language"),
    "csharp": ("tree_sitter_c_sharp", "language"),
}


@lru_cache(maxsize=16)
def get_parser(lang: str) -> Any | None:
    """Return a cached tree-sitter Parser for ``lang`` or None when
    unavailable. Never raises — callers must handle ``None``.

    Importing tree-sitter and the grammar lazily means the service
    starts even when no grammars are installed.
    """
    if lang not in _GRAMMAR_TABLE:
        return None
    try:
        from tree_sitter import Language, Parser  # noqa: PLC0415
    except ImportError:
        return None
    module_name, attr_name = _GRAMMAR_TABLE[lang]
    try:
        module = __import__(module_name)
    except ImportError:
        return None
    try:
        lang_callable = getattr(module, attr_name)
        # tree-sitter 0.23+ requires Language(<capsule>) wrap; older
        # releases accepted a raw int. We try the modern API first.
        ts_language = Language(lang_callable())
    except Exception:
        return None
    return Parser(ts_language)


def is_supported(lang: str) -> bool:
    """True when a tree-sitter parser for ``lang`` is loadable."""
    return get_parser(lang) is not None
