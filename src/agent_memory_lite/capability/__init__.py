"""Agent capability lifecycle: maturity scoring + usage tracking.

The ``capability`` package owns the *evolution* of skills, roles, and
playbooks: how confidence updates from observed outcomes, how stale
capabilities are detected, and where the single chokepoint for invocation
counters lives. It deliberately does not own SQL schema definition (that
is in ``repositories/capabilities_*``) or any agent-facing API surface;
it sits behind canonical v3 workflows such as plan-step outcome feeding.
"""
