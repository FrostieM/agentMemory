"""Phase 4 helper: ReDoS-defended payload pattern matching for reflex rules.

Extracted from ``reflex_check`` to keep that module small. The matcher is a
pure function with no dependency on the rest of the reflex pipeline.
"""

from __future__ import annotations

import re

_MAX_PATTERN_LEN = 256
_MAX_CANDIDATE_LEN = 4096


def _payload_matches_pattern(pattern: str, tool_payload: dict[str, object]) -> bool:
    """Empty pattern matches anything; otherwise regex-search common string fields.

    The payload shape varies wildly per tool, so we test the regex against
    each candidate field independently rather than concatenating them
    (which would break end-of-string anchors like ``\\.py$``).

    ReDoS defense:
    - patterns longer than ``_MAX_PATTERN_LEN`` are rejected outright
      (typical reflex rule pattern is < 40 chars; 256 is generous)
    - candidate strings are truncated to ``_MAX_CANDIDATE_LEN`` so an
      attacker-controlled command line cannot blow up backtracking
    - invalid regex silently fails (rule doesn't fire) rather than
      raising into the PreToolUse hook
    """
    if not pattern:
        return True
    if len(pattern) > _MAX_PATTERN_LEN:
        return False
    candidates: list[str] = []
    for key in ("file_path", "pattern", "command", "query", "path", "name"):
        value = tool_payload.get(key)
        if value is None:
            continue
        as_str = str(value).strip()
        if as_str:
            candidates.append(as_str[:_MAX_CANDIDATE_LEN])
    if not candidates:
        return False
    try:
        compiled = re.compile(pattern)
    except re.error:
        return False
    return any(compiled.search(c) for c in candidates)
