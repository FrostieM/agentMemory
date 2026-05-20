"""Shape-based secret patterns.

Each rule matches a token that has a recognizable structure regardless of context.
Whole-match redaction: the entire match is replaced.
"""

from __future__ import annotations

import re

SHAPE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{20,}")),
    ("github_pat", re.compile(r"gh[psour]_[A-Za-z0-9]{30,}")),
    ("slack_token", re.compile(r"xox[bpars]-[A-Za-z0-9-]{10,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{8,}\b"),
    ),
    (
        "private_key_block",
        re.compile(
            r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY( BLOCK)?-----"
            r"[\s\S]+?-----END (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY( BLOCK)?-----"
        ),
    ),
    # v3.5 sector-3 audit-followup: shapes the auditor flagged as
    # missing. Real-world `.env` / shell-command leakage paths.
    (
        "stripe_key",
        re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{20,}\b"),
    ),
    (
        "basic_auth_url",
        re.compile(r"https?://[^:@\s/]+:[^@\s/]+@\S+"),
    ),
    (
        "gcp_service_account_hint",
        re.compile(r'"type"\s*:\s*"service_account"'),
    ),
    # Generic Bearer tokens not already caught by jwt — common shape.
    (
        "bearer_token",
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE),
    ),
)
