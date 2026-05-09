"""Shared dataclass for code chunks across the Python AST and tree-sitter
chunking helpers. Lives in its own module so ``code_python.py`` and
``code_ts.py`` can both import it without a circular dep on ``code.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CodeChunk:
    text: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    symbols: list[str]
    # 1.4.0 symbol metadata. Optional so the legacy fallback path
    # (token-window split) can still emit chunks without manufacturing
    # a fake symbol kind / qualified name.
    symbol_kind: str | None = None
    qualified_name: str | None = None
    parent_qualified_name: str | None = None
    language: str | None = None
    extra_symbols: list[str] = field(default_factory=list)
