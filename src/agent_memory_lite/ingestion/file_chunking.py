"""Language-aware chunking helpers for the file ingest pipeline.

Split out of ``file_pipeline.py`` so the orchestrator stays under the
SLOC ceiling. Each helper picks the right chunker (markdown / code /
text) based on the language hint and produces a uniform tuple shape
the pipeline can iterate over.
"""

from __future__ import annotations

from agent_memory_lite.chunking.code import chunk_code
from agent_memory_lite.chunking.markdown import chunk_markdown
from agent_memory_lite.chunking.symbols import extract_symbols
from agent_memory_lite.chunking.text import chunk_text
from agent_memory_lite.models.enums import ChunkKind

CODE_LANGS: frozenset[str] = frozenset(
    {"python", "javascript", "typescript", "go", "rust", "java", "ruby", "kotlin"}
)


def chunk_for_kind(
    text: str, *, language: str | None
) -> list[tuple[str, int | None, int | None, list[str]]]:
    """Return ``(text, line_start, line_end, symbols)`` tuples per chunk."""
    lang = (language or "").lower()
    out: list[tuple[str, int | None, int | None, list[str]]] = []
    if lang == "markdown":
        for md_chunk in chunk_markdown(text):
            symbols = [md_chunk.heading] if md_chunk.heading else []
            out.append((md_chunk.text, md_chunk.line_start, md_chunk.line_end, symbols))
        return out
    if lang in CODE_LANGS:
        for code_chunk in chunk_code(text, language=lang):
            out.append(
                (
                    code_chunk.text,
                    code_chunk.line_start,
                    code_chunk.line_end,
                    list(code_chunk.symbols),
                )
            )
        return out
    for text_chunk in chunk_text(text):
        out.append(
            (
                text_chunk.text,
                text_chunk.line_start,
                text_chunk.line_end,
                extract_symbols(text_chunk.text),
            )
        )
    return out


def chunk_kind_for(language: str | None) -> ChunkKind:
    lang = (language or "").lower()
    if lang in CODE_LANGS:
        return ChunkKind.CODE
    if lang == "markdown":
        return ChunkKind.DOC
    return ChunkKind.DOC
