"""Keyword-based secret patterns.

These match `key = value` style configuration leaks. The negative lookahead
`(?!<<REDACTED)` keeps the redactor idempotent — already-redacted markers do not
re-match.
"""

from __future__ import annotations

import re

KEYWORD_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "password_kv",
        re.compile(
            r"(?i)\b(?:password|passwd|pwd)\s*[:=]\s*(?!<<REDACTED)\S+",
        ),
    ),
    (
        "api_key_kv",
        re.compile(
            r"(?i)\b(?:api[_-]?key|apikey|access[_-]?key)\s*[:=]\s*(?!<<REDACTED)\S+",
        ),
    ),
    (
        "secret_kv",
        re.compile(
            r"(?i)\b(?:secret|secret[_-]?key|client[_-]?secret)\s*[:=]\s*(?!<<REDACTED)\S+",
        ),
    ),
    (
        "token_kv",
        re.compile(
            r"(?i)\b(?:token|auth[_-]?token|access[_-]?token|bearer[_-]?token)"
            r"\s*[:=]\s*(?!<<REDACTED)\S+",
        ),
    ),
    (
        "bearer_header",
        re.compile(
            r"(?i)\bAuthorization\s*:\s*Bearer\s+(?!<<REDACTED)\S+",
        ),
    ),
    (
        "api_key_header",
        re.compile(
            r"(?i)\bX-API-Key\s*:\s*(?!<<REDACTED)\S+",
        ),
    ),
    (
        "db_url_password",
        re.compile(
            r"\b(?:postgres(?:ql)?|mongodb(?:\+srv)?|mysql|mssql|redis|amqp)://"
            r"[^:@\s/]+:(?!<<REDACTED)[^@\s/]+@",
        ),
    ),
)
