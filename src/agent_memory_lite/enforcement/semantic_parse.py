"""Parse the strict-JSON verdict returned by the Ollama policy reviewer.

Split out of ``semantic_review`` so SLOC budgets stay healthy. The
parser tolerates two failure modes commonly observed from small local
models: fenced code blocks (```json``` wrappers) and prose around the
JSON. Anything we cannot parse defaults to ``(False, "")`` — fail-OPEN.
"""

from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def _strip_fences(content: str) -> str:
    match = _FENCE_RE.search(content)
    if match:
        return match.group(1).strip()
    start = content.find("{")
    end = content.rfind("}")
    if 0 <= start < end:
        return content[start : end + 1]
    return content.strip()


def parse_verdict(content: str) -> tuple[bool, str]:
    """Return ``(violates, why)`` from the model response; defaults to (False, '')."""
    if not content.strip():
        return False, ""
    cleaned = _strip_fences(content)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return False, ""
    if not isinstance(payload, dict):
        return False, ""
    violates = bool(payload.get("violates"))
    why = str(payload.get("why") or "").strip()
    return violates, why
