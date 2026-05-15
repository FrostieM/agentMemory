"""PreToolUse enforcement layer.

Claude Code calls ``scripts/pre_tool_use_check.py`` before every tool
invocation. The script delegates to this package to decide whether the
tool call violates an active pinned ``behavior_instruction`` tagged
``enforcement:mechanical`` (regex/AST checks) or ``enforcement:semantic``
(Ollama-based LLM review against the rule text).

The decision turns into exit code 0 (allow) or 2 (block, stderr is shown
to the model). Rules without an enforcement tag stay in the foreground
reminder layer and are not blocked here — see module docstring of
``rule_loader`` for the tag convention.

Architecture:

* ``verdict``         — dataclasses (RuleViolation, HookDecision).
* ``rule_loader``     — read tagged behavior_instructions from SQLite.
* ``mechanical_*``    — per-rule detectors, deterministic, ~ms each.
* ``mechanical_dispatch`` — orchestrates the mechanical layer.
* ``session_trail``   — read Claude Code transcript JSONL for prior tool calls.
* ``semantic_*``      — Ollama-driven semantic-rule check (slower, opt-in).
* ``dispatch``        — top-level: mechanical first, then semantic.
"""
