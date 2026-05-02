"""Stopword set for the hygiene tokenizer.

Lives in its own module so the long literal does not push
``hygiene_models.py`` over the 150-SLOC ceiling, regardless of how
ruff format chooses to lay out the frozenset literal.
"""

from __future__ import annotations

# fmt: off
STOPWORDS: frozenset[str] = frozenset({
    "about", "active", "across", "also", "and", "any", "are", "after", "agent",
    "app", "because", "behavior", "before", "between", "bot", "but", "call",
    "can", "check", "clear", "could", "current", "decision", "does", "done",
    "during", "event", "exact", "existing", "explicit", "explicitly", "file",
    "files", "for", "found", "flow", "from", "full", "general", "has", "have",
    "important", "includes", "instead", "into", "issue", "keep", "labels",
    "least", "make", "memory", "missing", "must", "new", "not", "object",
    "old", "only", "one", "over", "pass", "path", "phase", "plus", "rather",
    "real", "remains", "required", "research", "responsive", "row", "rows",
    "safety", "run", "runs", "same", "show", "shows", "should", "small",
    "state", "status", "still", "that", "the", "their", "there", "this",
    "too", "tool", "tools", "under", "used", "using", "via", "wait",
    "waiting", "when", "where", "while", "with", "without", "work", "would",
})
# fmt: on
