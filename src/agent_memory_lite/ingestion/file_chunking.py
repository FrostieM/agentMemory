"""Language-aware chunking helpers for the file ingest pipeline.

Split out of ``file_pipeline.py`` so the orchestrator stays under the
SLOC ceiling. Each helper picks the right chunker (markdown / code /
text) based on the language hint and produces a uniform record shape
the pipeline can iterate over.

1.4.0: ``IngestChunk`` carries optional symbol metadata (kind /
qualified_name / parent) so code chunks emit indexable rows for compact
v3 code-aware retrieval without losing existing text-chunk paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_memory_lite.chunking.code import chunk_code
from agent_memory_lite.chunking.markdown import chunk_markdown
from agent_memory_lite.chunking.symbols import extract_symbols
from agent_memory_lite.chunking.text import chunk_text
from agent_memory_lite.models.enums import ChunkKind

# 1.4.0: extended to the full set of tree-sitter-supported languages.
# Pre-1.4.0 the set was {python, javascript, typescript, go, rust, java,
# ruby, kotlin}; we drop ruby and kotlin (no tree-sitter grammar wired)
# and add cpp + csharp now that the dispatcher handles them.
CODE_LANGS: frozenset[str] = frozenset(
    {"python", "javascript", "typescript", "go", "rust", "java", "cpp", "csharp"}
)


@dataclass(frozen=True, slots=True)
class IngestChunk:
    text: str
    line_start: int | None
    line_end: int | None
    symbols: list[str]
    symbol_kind: str | None = None
    qualified_name: str | None = None
    parent_qualified_name: str | None = None
    extra_metadata: dict[str, object] = field(default_factory=dict)


def chunk_for_kind(text: str, *, language: str | None) -> list[IngestChunk]:
    """Return ``IngestChunk`` records, language-dispatched."""
    lang = (language or "").lower()
    out: list[IngestChunk] = []
    if lang == "markdown":
        for md_chunk in chunk_markdown(text):
            symbols = [md_chunk.heading] if md_chunk.heading else []
            out.append(
                IngestChunk(
                    text=md_chunk.text,
                    line_start=md_chunk.line_start,
                    line_end=md_chunk.line_end,
                    symbols=symbols,
                )
            )
        return out
    if lang in CODE_LANGS:
        for code_chunk in chunk_code(text, language=lang):
            extra: dict[str, object] = {}
            if code_chunk.extra_symbols:
                extra["extra_symbols"] = list(code_chunk.extra_symbols)
            out.append(
                IngestChunk(
                    text=code_chunk.text,
                    line_start=code_chunk.line_start,
                    line_end=code_chunk.line_end,
                    symbols=list(code_chunk.symbols),
                    symbol_kind=code_chunk.symbol_kind,
                    qualified_name=code_chunk.qualified_name,
                    parent_qualified_name=code_chunk.parent_qualified_name,
                    extra_metadata=extra,
                )
            )
        return out
    for text_chunk in chunk_text(text):
        out.append(
            IngestChunk(
                text=text_chunk.text,
                line_start=text_chunk.line_start,
                line_end=text_chunk.line_end,
                symbols=extract_symbols(text_chunk.text),
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
