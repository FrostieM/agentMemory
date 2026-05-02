"""Personally identifiable information rules.

PII redaction is opt-in (off by default). Email addresses are the only rule we
ship in v1; phones and physical addresses are too locale-specific to redact
reliably without false positives.
"""

from __future__ import annotations

import re

PII_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
)
