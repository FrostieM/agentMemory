"""Keyword-based secret patterns.

These match `key = value` style configuration leaks. The negative lookahead
`(?!<<REDACTED)` keeps the redactor idempotent — already-redacted markers do not
re-match.

Round-2 audit fix: the value used to be a bare ``\\S+``, which stops at the
first whitespace. A quoted secret with spaces — ``password = "my long key"``
— was only redacted up to the first space, leaving the tail (``long key"``)
stored cleartext + FTS-indexed. ``_VALUE`` now matches a fully-quoted value
as one unit, falling back to the unquoted single token. It deliberately does
NOT swallow the rest of the line (``[^\\r\\n]+``) — that would over-redact
prose after an unquoted value like ``password: hunter2 then more text``.
"""

from __future__ import annotations

import re

# A secret value: a double- or single-quoted string (may contain spaces), or
# an unquoted run of non-whitespace. Quoted alternatives come first so the
# regex engine prefers the whole-quoted-string match.
_VALUE = r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|\S+)"

KEYWORD_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "password_kv",
        re.compile(
            rf"(?i)\b(?:password|passwd|pwd)\s*[:=]\s*(?!<<REDACTED){_VALUE}",
        ),
    ),
    (
        "api_key_kv",
        re.compile(
            rf"(?i)\b(?:api[_-]?key|apikey|access[_-]?key)\s*[:=]\s*(?!<<REDACTED){_VALUE}",
        ),
    ),
    (
        "secret_kv",
        re.compile(
            rf"(?i)\b(?:secret|secret[_-]?key|client[_-]?secret)\s*[:=]\s*(?!<<REDACTED){_VALUE}",
        ),
    ),
    (
        "token_kv",
        re.compile(
            r"(?i)\b(?:token|auth[_-]?token|access[_-]?token|bearer[_-]?token)"
            rf"\s*[:=]\s*(?!<<REDACTED){_VALUE}",
        ),
    ),
    (
        "bearer_header",
        re.compile(
            rf"(?i)\bAuthorization\s*:\s*Bearer\s+(?!<<REDACTED){_VALUE}",
        ),
    ),
    (
        "api_key_header",
        re.compile(
            rf"(?i)\bX-API-Key\s*:\s*(?!<<REDACTED){_VALUE}",
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
