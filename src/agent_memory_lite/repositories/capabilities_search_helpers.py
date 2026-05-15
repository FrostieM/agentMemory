"""Shared search helpers for capability repos.

Split out so each per-kind repo (roles / skills / playbooks) imports
the same tokenizer + filter without duplicating code.

``_STOP_TOKENS`` filters out generic decoration words ("design",
"engineer", "system", "operator", "data") that appear in most
capability descriptions and otherwise dominate the token-overlap
ranker — letting roles like "Senior Frontend UX Architect" win
queries about strategy/deploy/backtest. Removing these biases the
ranker toward distinguishing terms ("backtest", "tier", "incident",
"replay") that actually shape role-to-task fit.
"""

from __future__ import annotations

import json
import re

_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)

# Curated decoration-noise stop list. Keep small and focused: each token
# here was observed to inflate matches for ambiguous queries during
# live capability-ranker smoke testing on 2026-05-15. Do NOT add
# project-domain words ("memory", "tier", "copybot", "strategy") —
# those carry signal and must keep their weight.
_STOP_TOKENS: frozenset[str] = frozenset(
    {
        # Articles / pronouns / generic connectives that pass the
        # length-filter (already mostly filtered, listed explicit so
        # any future regex tweak preserves intent).
        "the",
        "and",
        "but",
        "for",
        "with",
        "from",
        "this",
        "that",
        "into",
        "without",
        "within",
        "over",
        "under",
        # Common verbs in role/skill descriptions (don't carry domain signal).
        "use",
        "uses",
        "used",
        "make",
        "made",
        "have",
        "has",
        "had",
        "do",
        "does",
        "done",
        "get",
        "gets",
        "should",
        "would",
        "could",
        "may",
        "must",
        # Pronouns + temporal noise that survives the length filter
        "we",
        "us",
        "today",
        "tomorrow",
        "yesterday",
        "now",
        # Generic agent/engineering decoration that appears in most role
        # descriptions (this is the SIGNAL-KILLER set — see module docstring).
        "agent",
        "agents",
        "task",
        "tasks",
        "work",
        "works",
        "working",
        "system",
        "systems",
        "data",
        "code",
        "project",
        "step",
        "steps",
        "method",
        "methods",
        "approach",
        "process",
        "user",
        "users",
        "operator",
        "operators",
        "engineer",
        "engineering",
        "design",
        "designed",
        "designing",
        "designer",
        "manage",
        "managing",
        "management",
        "build",
        "building",
        "implement",
        "implementation",
        "interface",
        "interfaces",
        "complex",
        "runtime",
    }
)


def json_list(raw: str | None) -> list[str]:
    data = json.loads(raw or "[]")
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def tokens_from(query: str | None) -> list[str]:
    """Tokenize query, lowercase, length-filter, AND filter generic stop words.

    Stop-list removes high-frequency decoration tokens that pollute
    capability ranking. Distinguishing project-domain terms (e.g.
    "backtest", "tier", "calibration", "deploy") keep their weight.
    """
    if not query:
        return []
    return [
        token
        for token in (raw.lower() for raw in _TOKEN_RE.findall(query) if len(raw) > 1)
        if token not in _STOP_TOKENS
    ]


def contains_any(text: str, tokens: list[str]) -> bool:
    if not tokens:
        return True
    lower = text.lower()
    return any(token in lower for token in tokens)
