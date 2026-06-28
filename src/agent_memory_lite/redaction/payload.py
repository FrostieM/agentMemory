"""Redact secret shapes from the free-text fields of a write payload.

Used at the create choke point (``ingestion.canonical_writer.write_canonical``)
and the edit choke point (``storage.writer.edit``) so EVERY kind is covered --
the per-kind business writers (decision/theory/behavior/episode/capability)
redact their own fields, but the else-branch durable kinds (insight / skill /
concept / snapshot / code_digest) and ``issue`` route straight to
``storage.writer.write`` with no redaction, so a pasted secret landed cleartext
on disk AND in the durable_fts BM25 index (reliability audit 2026-06-26).

Redaction is recursive: a secret can hide in a free-text string, in a list
element (``aliases`` / ``tags`` / ``evidence`` -> serialized to a ``*_json``
column), or in a nested dict -- mirroring ``episode_pipeline._redact_metadata``.
A pre-serialized ``*_json`` STRING is parsed, walked, and re-dumped (redacting
the raw blob would corrupt its structure). ``redact()`` is a no-op on a value
with no secret shape, so this is idempotent (re-redacting a self-redacting kind
costs nothing) and safe on structural columns -- but we still skip
identity/routing/temporal/numeric keys so a generated id can never be mangled.
"""

from __future__ import annotations

import json
from typing import Any

from agent_memory_lite.redaction.redactor import redact

# Keys whose value must pass through untouched: identity, routing, enum/status,
# temporal, and structured/numeric control fields.
_NO_REDACT_KEYS = frozenset(
    {
        "id",
        "workspace_id",
        "source_episode_id",
        "agent_id",
        "kind",
        "subtype",
        "status",
        "active",
        "pinned",
        "task_id",
        "parent_step_id",
        "target_id",
        "target_type",
        "supersedes",
        "supersedes_decision_id",
        "signature",
        "created_at",
        "updated_at",
        "valid_from",
        "valid_to",
        "snapshot_key",
        "insight_type",
        "language",
        "file_path",
    }
)
# NB intentionally NOT skipped (audit round 3): `category` / `severity` look
# enum-ish but are NOT enum-enforced at the v3 write boundary (only `episode`
# payloads are validated), and they are returned VERBATIM in the issue
# projection -- so a secret pasted there would surface cleartext on every
# memory_search/get. They are redacted (a no-op on a real 'bug'/'minor' value).
# Suffixes that mark an id / temporal / numeric column (never free text).
_NO_REDACT_SUFFIXES = ("_id", "_at", "_count", "_score", "_rank")
# Bound recursion so a pathologically-nested payload (a crafted ``*_json`` blob
# of thousands of nested arrays) cannot raise RecursionError into the write --
# past this depth a value is left untouched (audit round 2). Real memory rows
# are shallow; legitimate content never approaches this.
_MAX_REDACT_DEPTH = 25


def _redact_value(value: Any, _depth: int = 0) -> Any:
    """Recursively strip secret shapes: strings directly; list elements and dict
    values recursively; non-text (int/float/bool/None) passes through. Stops at
    ``_MAX_REDACT_DEPTH`` so a hostile deep nest can't blow the stack."""
    if _depth >= _MAX_REDACT_DEPTH:
        return value
    if isinstance(value, str):
        return redact(value).text if value else value
    if isinstance(value, list):
        return [_redact_value(v, _depth + 1) for v in value]
    if isinstance(value, dict):
        return {k: _redact_value(v, _depth + 1) for k, v in value.items()}
    return value


def _redact_json_string(value: str) -> str:
    """Redact secrets inside a serialized-JSON string by parsing, recursively
    redacting, and re-dumping. A non-JSON value (or one too deep to parse
    safely) is returned unchanged -- redacting the raw blob could corrupt its
    structure (a value regex can eat a closing quote), so we only touch it when
    it round-trips through json. RecursionError (deeply-nested JSON) is treated
    like invalid JSON: leave it, never crash the write (audit round 2)."""
    if not value:
        return value
    try:
        parsed = json.loads(value)
        return json.dumps(_redact_value(parsed))
    except (TypeError, ValueError, RecursionError):
        return value


def redact_freetext_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``payload`` with secret shapes stripped from every
    free-text value -- including list elements and nested dict values, and the
    contents of pre-serialized ``*_json`` strings. Identity/routing/temporal/
    numeric fields pass through unchanged."""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _NO_REDACT_KEYS or key.endswith(_NO_REDACT_SUFFIXES):
            out[key] = value
        elif key.endswith("_json") and isinstance(value, str):
            out[key] = _redact_json_string(value)
        else:
            out[key] = _redact_value(value)
    return out
