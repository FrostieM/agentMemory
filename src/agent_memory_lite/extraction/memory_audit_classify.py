"""Tool-name classification + result dataclasses for the memory audit.

Pure data layer for ``memory_audit_prompt``. No I/O, no network.
"""

from __future__ import annotations

from dataclasses import dataclass

_FILE_MUTATION_TOOLS = frozenset({"Edit", "Write", "NotebookEdit", "Bash"})
_FILE_READ_TOOLS = frozenset({"Read", "Grep", "Glob"})
_MEMORY_WRITE_NAMES = frozenset(
    {
        "memory_write",
        "memory_edit",
        "memory_pin",
        "memory_archive",
        "memory_rollback",
    }
)


@dataclass(frozen=True, slots=True)
class TurnToolStats:
    """Counts of classified tool_use blocks in a single assistant turn."""

    file_mutations: int
    file_reads: int
    memory_writes: int
    memory_reads: int
    other: int
    tool_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuditDecision:
    """Audit result. ``inject=True`` -> hook emits prompt."""

    inject: bool
    prompt: str


def _bare_tool_name(name: str) -> str:
    """Strip ``mcp__<server>__`` prefix from MCP tool names."""
    if "__" not in name:
        return name
    return name.rsplit("__", 1)[-1]


def classify_tool(name: str) -> str:
    """Bucket a tool name into file_mutation / file_read / memory_write / memory_read / other."""
    if name in _FILE_MUTATION_TOOLS:
        return "file_mutation"
    if name in _FILE_READ_TOOLS:
        return "file_read"
    bare = _bare_tool_name(name)
    if bare in _MEMORY_WRITE_NAMES:
        return "memory_write"
    if bare.startswith("memory_"):
        return "memory_read"
    return "other"


def count_tools_in_turn(content: list[object]) -> TurnToolStats:
    """Walk assistant-message content blocks and tally tool_use by classification."""
    mutations = reads = mem_writes = mem_reads = other = 0
    names: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use":
            continue
        name = str(block.get("name", ""))
        names.append(name)
        cls = classify_tool(name)
        if cls == "file_mutation":
            mutations += 1
        elif cls == "file_read":
            reads += 1
        elif cls == "memory_write":
            mem_writes += 1
        elif cls == "memory_read":
            mem_reads += 1
        else:
            other += 1
    return TurnToolStats(
        file_mutations=mutations,
        file_reads=reads,
        memory_writes=mem_writes,
        memory_reads=mem_reads,
        other=other,
        tool_names=tuple(names),
    )
