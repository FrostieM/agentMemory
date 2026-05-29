"""Guard against kind-registry drift (plan step 10.4).

A memory kind must be wired in EVERY registry or it silently half-works.
This pins the invariant so the next "added to one map but not another" bug
— the class that already hit the PreToolUse matcher tokens, the version
constants (pyproject vs version.py), and the issue kind's writer-vs-reader
table maps — fails a fast unit test instead of shipping silently.

Registries:
- MCP ``_KINDS``         — the agent write/get/edit/archive surface
- writer ``_KIND_META``  — kind -> (table, gist_src, gist_dst)
- reader ``_KIND_TABLES``— kind -> (table, id_col) for get/list/search
- projections ``_PROJECTORS`` — kind -> compact projection builder
"""

from __future__ import annotations

from agent_memory_lite.mcp.stdio_tools_memory import _KINDS as MCP_KINDS
from agent_memory_lite.storage.projections import _PROJECTORS
from agent_memory_lite.storage.reader import _KIND_TABLES
from agent_memory_lite.storage.writer import _KIND_META


def test_every_agent_kind_is_wired_in_all_registries() -> None:
    """Every kind the agent can write/get via MCP must be fully wired."""
    for kind in MCP_KINDS:
        assert kind in _KIND_META, f"{kind!r} in MCP _KINDS but missing from writer._KIND_META"
        assert kind in _KIND_TABLES, f"{kind!r} in MCP _KINDS but missing from reader._KIND_TABLES"
        assert kind in _PROJECTORS, (
            f"{kind!r} in MCP _KINDS but missing from projections._PROJECTORS"
        )


def test_writer_and_reader_tables_agree() -> None:
    """A kind present in both maps must point at the SAME physical table."""
    for kind, (writer_table, _src, _dst) in _KIND_META.items():
        if kind in _KIND_TABLES:
            reader_table, _id_col = _KIND_TABLES[kind]
            assert writer_table == reader_table, (
                f"{kind!r}: writer table {writer_table!r} != reader table {reader_table!r}"
            )


def test_projectable_kinds_have_a_reader_table() -> None:
    """A projectable kind must be fetchable, or search/get cannot build it."""
    for kind in _PROJECTORS:
        assert kind in _KIND_TABLES, f"{kind!r} projectable but missing from reader._KIND_TABLES"
