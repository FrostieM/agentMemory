"""agent-memory-lite v3 surface — compact-projection memory layer.

Layered above the v2 storage substrate. Every tool returns compact
projections (~20-40 tokens per item) by default; full content is
opt-in via ``fields=`` parameter on ``memory_get``.

Subpackages:

* ``storage`` — projections, reader, writer, versioning on top of SQLite.
* ``cognition`` — brief, lint, advise, consolidation, digest worker.
* ``compat`` — v2 tool-name → v3 backend adapters.

See ``docs/V3_SCHEMA.md`` for the data model and
``C:/Users/Osino/.claude/plans/foamy-nibbling-ember.md`` for the plan.
"""
