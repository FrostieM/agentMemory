"""Agent capability lifecycle: maturity scoring + usage tracking.

The ``capability`` package owns the *evolution* of skills, roles, and
playbooks: how confidence updates from observed outcomes, how stale
capabilities are detected, and where the single chokepoint for invocation
counters lives. It deliberately does not own SQL schema definition (that
is in ``repositories/capabilities_*``) or HTTP shape (that is in
``api/routes/capabilities*``); it sits between them.
"""
