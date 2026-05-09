"""2.1.3: LLM-narrative wiring for the file digest builder.

Split from ``file_digest_builder.py`` to keep both modules under
the SLOC ceiling. Two helpers:

* ``top_targets`` — read the top inbound (callers) and outbound
  (callees) qualified_names for a file's symbols from
  ``symbol_edges``. Used as inline context for the LLM prompt.
* ``maybe_llm_narrative`` — opt-in LLM call gated on
  ``Settings.llm_narrative_enabled`` + minimum symbol count.
  Returns ``None`` when disabled / Ollama unreachable; caller
  falls back to heuristic.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.extraction.file_narrative_llm import (
    build_prompt,
    call_llm_narrative,
)


def top_targets(
    conn: sqlite3.Connection, *, workspace_id: str, qnames: list[str]
) -> tuple[list[str], list[str]]:
    """Return (inbound_callers, outbound_callees) qualified_names."""
    if not qnames:
        return [], []
    placeholders = ", ".join("?" * len(qnames))
    inbound = [
        str(r[0])
        for r in conn.execute(
            f"SELECT src_qualified_name FROM symbol_edges "
            f"WHERE workspace_id = ? AND dst_qualified_name IN ({placeholders}) "
            f"ORDER BY created_at DESC LIMIT 10",
            [workspace_id, *qnames],
        ).fetchall()
    ]
    outbound = [
        str(r[0])
        for r in conn.execute(
            f"SELECT dst_qualified_name FROM symbol_edges "
            f"WHERE workspace_id = ? AND src_qualified_name IN ({placeholders}) "
            f"ORDER BY created_at DESC LIMIT 10",
            [workspace_id, *qnames],
        ).fetchall()
    ]
    return inbound, outbound


def maybe_llm_narrative(
    *,
    settings: Settings | None,
    file_path: str,
    language: str | None,
    qnames: list[str],
    inbound_targets: list[str],
    outbound_targets: list[str],
) -> str | None:
    """Opt-in LLM call. Returns None when disabled / too few symbols
    / Ollama unreachable; caller falls back to heuristic."""
    if settings is None or not settings.llm_narrative_enabled:
        return None
    if len(qnames) < settings.llm_narrative_min_symbols:
        return None
    prompt = build_prompt(
        file_path=file_path,
        language=language,
        qualified_names=qnames,
        inbound_targets=inbound_targets,
        outbound_targets=outbound_targets,
        max_chars=settings.llm_narrative_max_input_chars,
    )
    return call_llm_narrative(
        settings, prompt=prompt, timeout_sec=settings.llm_narrative_timeout_sec
    )
