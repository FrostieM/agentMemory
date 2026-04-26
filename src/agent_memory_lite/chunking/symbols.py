"""Best-effort symbol extraction.

For Python we use the standard `ast` module. For other languages we fall back
to a regex that catches the common `def`/`function`/`class` patterns. Failures
return an empty list — symbols are best-effort metadata, not load-bearing.
"""

from __future__ import annotations

import ast
import re

_FALLBACK_RE = re.compile(
    r"(?m)^\s*(?:export\s+)?(?:async\s+)?(?:function|class|def|interface|type)\s+([A-Za-z_][A-Za-z0-9_]*)"
)


def extract_symbols(text: str, *, language: str | None = None) -> list[str]:
    if not text.strip():
        return []
    if (language or "").lower() == "python":
        return _python_symbols(text)
    return _regex_symbols(text)


def _python_symbols(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _regex_symbols(text)
    symbols: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and node.name not in symbols
        ):
            symbols.append(node.name)
    return symbols


def _regex_symbols(text: str) -> list[str]:
    found: list[str] = []
    for match in _FALLBACK_RE.finditer(text):
        name = match.group(1)
        if name and name not in found:
            found.append(name)
    return found
