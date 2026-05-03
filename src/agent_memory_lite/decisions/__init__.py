"""Decision domain helpers — lineage walker, candidate bridge.

The ``decisions`` package owns higher-level semantics around the decisions
table that don't fit in a thin repository: walking the supersedes chain,
computing confidence trends across that chain, and the theory -> candidate
bridge. SQL access still goes through ``repositories/``; this package
composes those calls.
"""
