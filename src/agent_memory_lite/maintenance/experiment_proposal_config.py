"""Env-flag readers for v3.1 experiment proposal.

Lifted out to keep ``experiment_proposal`` under the 150-SLOC ceiling.
All four flags are read fresh on every call so live operator tuning
takes effect without a process restart.
"""

from __future__ import annotations

import os


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int, *, floor: int = 1) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(floor, value)


def is_enabled() -> bool:
    return _bool_env("MEMORY_EXPERIMENT_PROPOSAL_ENABLED", True)


def conf_window() -> tuple[float, float]:
    """Return the (lo, hi) confidence window, clamped to [0, 1].

    Vector1-audit M5: an operator who flips CONF_MIN > CONF_MAX would
    otherwise produce a SQL window that excludes every row silently.
    We sort the pair so the scanner stays well-defined.
    """
    lo = max(0.0, min(_float_env("MEMORY_EXPERIMENT_PROPOSAL_CONF_MIN", 0.4), 1.0))
    hi = max(0.0, min(_float_env("MEMORY_EXPERIMENT_PROPOSAL_CONF_MAX", 0.7), 1.0))
    if lo > hi:
        lo, hi = hi, lo
    return (lo, hi)


def emit_limit() -> int:
    return _int_env("MEMORY_EXPERIMENT_PROPOSAL_LIMIT", 3, floor=1)


def min_evidence() -> int:
    """v3.3 roadmap gate: an insight must have at least ``N`` source
    episodes before V1 generates a proposal from it. Stops the heuristic
    consolidation pass from turning 1-2 episodes into a polished LLM
    hypothesis. Default 3 per ``docs/V3_1_BREAKTHROUGH_ROADMAP.md``."""
    return _int_env("MEMORY_EXPERIMENT_PROPOSAL_MIN_EVIDENCE", 3, floor=1)
