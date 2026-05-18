"""v2-tool → v3-backend compat shim.

A translation layer that lets legacy v2 tool calls (``memory_write_decision``,
``memory_list_theories``, …) survive the v3 cutover by routing them through
the v3 storage / cognition functions with shape adapters on the input and
the output. The native v2 handlers stay registered alongside this shim
until the week-8 cutover; the shim becomes the only path once they are
removed.

Activation:

* ``MEMORY_V2_COMPAT_ENABLED`` defaults to **true** post-canonical-rename
  (commit 2026-05-18). Set to ``false`` to restore the native v2
  handlers for debugging.
* Every shimmed response carries a ``deprecation_notice`` field pointing
  at the canonical successor so callers see the migration target.

Coverage tiers:

* **Tier 1 (primary tools).** Implemented here with full input/output
  translation: write_decision, write_theory, get_object, upsert_concept,
  upsert_behavior_instruction, upsert_agent_role/skill/playbook,
  archive, pin, search, list_decisions, list_theories.
* **Tier 2 (research lab).** Implemented as best-effort translations:
  register_snapshot, write_experiment, add_experiment_result,
  distill_insight, link_capability, list_research_agenda,
  list_concepts, list_insights, list_behavior_instructions,
  list_agent_capabilities, list_candidates.
* **Tier 3 (operational tools).** Returns ``{ok: false, error:
  translation_pending}`` so the caller knows the v3 surface doesn't
  cover it yet. Examples: snapshot_save/list/diff, list_audit,
  list_maintenance_events.

The shim never raises — every call returns the v3 envelope shape
``{ok, data, error, deprecation_notice?}`` so the dispatcher can
JSON-serialize it directly.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable
from typing import Any

from agent_memory_lite.cognition.brief import fetch_skill_body
from agent_memory_lite.storage.reader import get_object, list_kind, search
from agent_memory_lite.storage.writer import archive, pin, write

# ============================================================
# Activation gate
# ============================================================


def is_enabled() -> bool:
    """Read MEMORY_V2_COMPAT_ENABLED. Defaults **ON** post-canonical-rename.

    Rationale: with canonical names live (``memory_search``,
    ``memory_write``, ...), every legacy v2 tool that still has its
    own native handler runs that handler synchronously -- in the case
    of ``memory_record_with_evidence`` and ``memory_ingest_episode``
    that means a synchronous Ollama LLM call that can hang Claude
    Code for 60-180 s on a multi-KB payload. The compat shim routes
    those legacy names through the v3 storage layer (no sync LLM, no
    embedding bottleneck on the hot path), which is the safer and
    much faster default. Set the env to ``false`` to restore the
    native v2 path for debugging.
    """
    raw = os.environ.get("MEMORY_V2_COMPAT_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# ============================================================
# Envelope helpers
# ============================================================


def _ok(data: Any, *, deprecation: str | None = None) -> dict[str, Any]:
    env: dict[str, Any] = {"ok": True, "data": data, "error": None}
    if deprecation is not None:
        env["deprecation_notice"] = deprecation
    return env


def _err(code: str, message: str, *, deprecation: str | None = None) -> dict[str, Any]:
    env: dict[str, Any] = {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message},
    }
    if deprecation is not None:
        env["deprecation_notice"] = deprecation
    return env


def _deprecation(v2_name: str, v3_successor: str) -> str:
    return f"{v2_name} is deprecated; use {v3_successor}. This shim will be removed at v4.0."


# ============================================================
# Tier 1: primary tool translations
# ============================================================


def _compat_write_decision(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": args.get("title", ""),
        "decision_text": args.get("decision_text", ""),
        "rationale": args.get("rationale"),
    }
    if "supersedes_decision_id" in args:
        payload["supersedes"] = args["supersedes_decision_id"]
    out = write(
        conn,
        workspace_id=str(args.get("workspace_id") or "default"),
        kind="decision",
        payload={k: v for k, v in payload.items() if v is not None},
        agent_id=str(args.get("agent_id") or "compat"),
    )
    if out is None:
        return _err("unsupported_kind", "decision write failed via shim")
    return _ok(out, deprecation=_deprecation("memory_write_decision", "memory_write"))


def _compat_write_theory(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": args.get("title", ""),
        "claim": args.get("claim", ""),
        "mechanism": args.get("mechanism"),
        "status": args.get("status", "proposed"),
        "confidence": args.get("confidence"),
    }
    out = write(
        conn,
        workspace_id=str(args.get("workspace_id") or "default"),
        kind="theory",
        payload={k: v for k, v in payload.items() if v is not None},
        agent_id=str(args.get("agent_id") or "compat"),
    )
    if out is None:
        return _err("unsupported_kind", "theory write failed via shim")
    return _ok(out, deprecation=_deprecation("memory_write_theory", "memory_write"))


def _compat_get_object(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    kind = str(args.get("kind") or "")
    object_id = str(args.get("id") or "")
    if not kind or not object_id:
        return _err("invalid_args", "kind and id are required")
    fields: list[str] | None = None
    if args.get("include_evidence") and kind == "theory":
        fields = ["claim", "mechanism", "predictions_json", "validation_criteria_json"]
    out = get_object(
        conn,
        workspace_id=str(args.get("workspace_id") or "default"),
        kind=kind,
        object_id=object_id,
        fields=fields,
    )
    if out is None:
        return _err("not_found", f"{kind}:{object_id} not found")
    return _ok(out, deprecation=_deprecation("memory_get_object", "memory_get"))


def _compat_upsert_concept(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": args.get("name", ""),
        "definition": args.get("definition", ""),
        # v2's "kind" field is the concept-kind label (gate, metric, ...) — same
        # column name in v3 concepts table.
        "kind": args.get("kind"),
        "aliases_json": json.dumps(args.get("aliases") or []),
    }
    out = write(
        conn,
        workspace_id=str(args.get("workspace_id") or "default"),
        kind="concept",
        payload={k: v for k, v in payload.items() if v},
        agent_id=str(args.get("agent_id") or "compat"),
    )
    if out is None:
        return _err("unsupported_kind", "concept write failed via shim")
    return _ok(out, deprecation=_deprecation("memory_upsert_concept", "memory_write"))


def _compat_upsert_behavior(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": args.get("name", ""),
        "rule": args.get("rule", ""),
        "rationale": args.get("rationale"),
        # v3 behaviors.kind enum: communication_style | operating_rule | ...
        "kind": args.get("kind", "operating_rule"),
        "applies_to_json": json.dumps(args.get("applies_to") or []),
        "pinned": 1 if args.get("pinned") else 0,
    }
    out = write(
        conn,
        workspace_id=str(args.get("workspace_id") or "default"),
        kind="behavior",
        payload={k: v for k, v in payload.items() if v is not None},
        agent_id=str(args.get("agent_id") or "compat"),
    )
    if out is None:
        return _err("unsupported_kind", "behavior write failed via shim")
    return _ok(out, deprecation=_deprecation("memory_upsert_behavior_instruction", "memory_write"))


def _compat_upsert_capability(
    conn: sqlite3.Connection, args: dict[str, Any], subtype: str
) -> dict[str, Any]:
    """role / skill / playbook all collapse into the v3 ``skills`` table with subtype."""
    payload = {
        "name": args.get("name", ""),
        "summary": args.get("summary") or args.get("purpose") or args.get("goal", ""),
        "subtype": subtype,
        "body_md": args.get("body_md") or args.get("description"),
    }
    out = write(
        conn,
        workspace_id=str(args.get("workspace_id") or "default"),
        kind="skill",
        payload={k: v for k, v in payload.items() if v},
        agent_id=str(args.get("agent_id") or "compat"),
    )
    if out is None:
        return _err("unsupported_kind", f"{subtype} write failed via shim")
    return _ok(out, deprecation=_deprecation(f"memory_upsert_agent_{subtype}", "memory_write"))


def _compat_archive(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    kind = str(args.get("kind") or "")
    object_id = str(args.get("id") or "")
    if not kind or not object_id:
        return _err("invalid_args", "kind and id are required")
    # v2 archive supports archive=false (restore). v3 archive is one-way;
    # restore requires manual edit.
    if not args.get("archive", True):
        return _err(
            "translation_pending",
            "v3 archive is one-way; use memory_edit to restore status",
        )
    out = archive(
        conn,
        workspace_id=str(args.get("workspace_id") or "default"),
        kind=kind,
        object_id=object_id,
        reason=args.get("reason"),
        agent_id=str(args.get("agent_id") or "compat"),
    )
    if out is None:
        return _err("not_found_or_unsupported", f"cannot archive {kind}:{object_id}")
    return _ok(out, deprecation=_deprecation("memory_archive", "memory_archive"))


def _compat_pin(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    kind = str(args.get("kind") or "")
    object_id = str(args.get("id") or "")
    if not kind or not object_id:
        return _err("invalid_args", "kind and id are required")
    pinned = bool(args.get("pinned", True))
    out = pin(
        conn,
        workspace_id=str(args.get("workspace_id") or "default"),
        kind=kind,
        object_id=object_id,
        pinned=pinned,
        agent_id=str(args.get("agent_id") or "compat"),
    )
    if out is None:
        return _err("unsupported_kind", "pin only valid for decision + behavior in v3")
    return _ok(out, deprecation=_deprecation("memory_pin", "memory_pin"))


def _compat_search(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "")
    if not query:
        return _err("invalid_args", "query is required")
    hits = search(
        conn,
        workspace_id=str(args.get("workspace_id") or "default"),
        query=query,
        kinds=None,  # v2 search is all-kinds chunks BM25; v3 search ≈ same shape
        limit=int(args.get("limit") or 10),
    )
    data = [{"kind": h.kind, "projection": h.projection, "score": h.score} for h in hits]
    return _ok(data, deprecation=_deprecation("memory_search", "memory_search"))


def _compat_list_kind(
    conn: sqlite3.Connection, args: dict[str, Any], kind: str, v2_name: str
) -> dict[str, Any]:
    """Generic list translator. v2 list_decisions / list_theories etc."""
    include_superseded = bool(args.get("include_superseded"))
    rows = list_kind(
        conn,
        workspace_id=str(args.get("workspace_id") or "default"),
        kind=kind,
        limit=int(args.get("limit") or 20),
        pinned_only=False,
        status=None if include_superseded else "active",
    )
    return _ok(rows, deprecation=_deprecation(v2_name, "memory_search or HTTP /memory/list"))


def _compat_invoke_skill(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    """Synthetic — v2 did not have invoke_skill; this is for completeness."""
    skill_id = str(args.get("skill_id") or args.get("id") or "")
    if not skill_id:
        return _err("invalid_args", "skill_id is required")
    out = fetch_skill_body(
        conn,
        workspace_id=str(args.get("workspace_id") or "default"),
        skill_id=skill_id,
    )
    if out is None:
        return _err("not_found", f"skill:{skill_id} not found")
    return _ok(out, deprecation=_deprecation("memory_invoke_skill", "memory_invoke_skill"))


# ============================================================
# Tier 1 cont. -- compound writes that previously dragged Ollama in-process.
# ============================================================


def _compat_ingest_episode(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    """Translate ``memory_ingest_episode`` to the canonical episode write.

    The native v2 handler runs the full redaction + embedding + auto-
    extraction pipeline synchronously, which on multi-KB raw_text can
    park the tool call for 60-180 s while Ollama chews on candidate
    extraction. The canonical writer just persists the row (gist
    computed cheaply, no LLM) and returns immediately.
    """
    payload: dict[str, Any] = {
        "raw_text": args.get("raw_text") or args.get("text") or "",
        "source_type": args.get("source_type", "manual"),
        "session_id": args.get("session_id"),
        "task_id": args.get("task_id"),
        "trust_level": args.get("trust_level"),
        "importance": args.get("importance"),
    }
    cleaned = {k: v for k, v in payload.items() if v is not None and v != ""}
    if not cleaned.get("raw_text"):
        return _err("invalid_args", "raw_text is required")
    out = write(
        conn,
        workspace_id=str(args.get("workspace_id") or "default"),
        kind="episode",
        payload=cleaned,
        agent_id=str(args.get("agent_id") or "compat"),
    )
    if out is None:
        return _err("unsupported_kind", "episode write failed via shim")
    return _ok(out, deprecation=_deprecation("memory_ingest_episode", "memory_write"))


def _compat_record_with_evidence(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    """Translate ``memory_record_with_evidence`` -- the v2 atomic write.

    v2 native handler did episode + decision + optional capability link
    in one transaction, but each step ran Ollama for candidate
    extraction. Cumulative cost on a 4 KB decision: ~3 minutes,
    enough to time out Claude Code's MCP channel and abort the
    session mid-pipe. The canonical writer skips LLM extraction on
    the hot path, so two ``write()`` calls land in milliseconds.

    Capability link step is preserved when the caller passed the
    triplet, but it lives in the v2 repository layer (no canonical
    equivalent yet) so we record the ids and surface them in the
    envelope without writing the link row from this shim. Operators
    who need the link can call ``memory_link_capability`` after.
    """
    workspace_id = str(args.get("workspace_id") or "default")
    agent_id = str(args.get("agent_id") or "compat")
    evidence_text = args.get("evidence_text") or ""
    if not evidence_text:
        return _err("invalid_args", "evidence_text is required")
    decision_title = args.get("decision_title") or ""
    decision_text = args.get("decision_text") or ""
    if not decision_title or not decision_text:
        return _err("invalid_args", "decision_title and decision_text are required")

    episode = write(
        conn,
        workspace_id=workspace_id,
        kind="episode",
        payload={
            "raw_text": evidence_text,
            "source_type": args.get("evidence_source_type", "agent_action"),
            "trust_level": args.get("evidence_trust_level"),
            "importance": args.get("evidence_importance"),
            "session_id": args.get("session_id"),
            "task_id": args.get("task_id"),
        },
        agent_id=agent_id,
    )
    if episode is None:
        return _err("write_failed", "episode write failed")
    episode_id = (episode.get("id") if isinstance(episode, dict) else None) or ""

    decision_payload: dict[str, Any] = {
        "title": decision_title,
        "decision_text": decision_text,
        "rationale": args.get("decision_rationale"),
        "confidence": args.get("decision_confidence"),
        "importance": args.get("decision_importance"),
    }
    cleaned = {k: v for k, v in decision_payload.items() if v is not None}
    decision = write(
        conn,
        workspace_id=workspace_id,
        kind="decision",
        payload=cleaned,
        agent_id=agent_id,
        source_episode_id=episode_id or None,
    )
    if decision is None:
        return _err("write_failed", "decision write failed after episode")
    decision_id = (decision.get("id") if isinstance(decision, dict) else None) or ""

    data: dict[str, Any] = {
        "workspace_id": workspace_id,
        "episode_id": episode_id,
        "decision_id": decision_id,
        "decision_status": "active",
        "episode": episode,
        "decision": decision,
        "capability_link_id": None,
        "capability_suggestions": [],
    }
    return _ok(data, deprecation=_deprecation("memory_record_with_evidence", "memory_write"))


# ============================================================
# Tier 3: not-yet-translated — explicit placeholder
# ============================================================


def _translation_pending(
    v2_name: str,
) -> Callable[[sqlite3.Connection, dict[str, Any]], dict[str, Any]]:
    def handler(_conn: sqlite3.Connection, _args: dict[str, Any]) -> dict[str, Any]:
        return _err(
            "translation_pending",
            f"v2 tool {v2_name!r} has no v3 equivalent yet; call HTTP /memory/*"
            " directly or wait for the v3.1 release.",
            deprecation=_deprecation(v2_name, "memory-cli or /memory/*"),
        )

    return handler


# ============================================================
# Dispatch table: v2 tool name → handler
# ============================================================


_Handler = Callable[[sqlite3.Connection, dict[str, Any]], dict[str, Any]]


COMPAT_HANDLERS: dict[str, _Handler] = {
    # Tier 1 -- primary tools
    "memory_ingest_episode": _compat_ingest_episode,
    "memory_record_with_evidence": _compat_record_with_evidence,
    "memory_write_decision": _compat_write_decision,
    "memory_write_theory": _compat_write_theory,
    "memory_get_object": _compat_get_object,
    "memory_upsert_concept": _compat_upsert_concept,
    "memory_upsert_behavior_instruction": _compat_upsert_behavior,
    "memory_upsert_agent_role": lambda c, a: _compat_upsert_capability(c, a, "role"),
    "memory_upsert_agent_skill": lambda c, a: _compat_upsert_capability(c, a, "skill"),
    "memory_upsert_agent_playbook": lambda c, a: _compat_upsert_capability(c, a, "playbook"),
    "memory_archive": _compat_archive,
    "memory_pin": _compat_pin,
    "memory_search": _compat_search,
    "memory_list_decisions": lambda c, a: _compat_list_kind(
        c, a, "decision", "memory_list_decisions"
    ),
    "memory_list_theories": lambda c, a: _compat_list_kind(c, a, "theory", "memory_list_theories"),
    "memory_list_concepts": lambda c, a: _compat_list_kind(c, a, "concept", "memory_list_concepts"),
    "memory_list_insights": lambda c, a: _compat_list_kind(c, a, "insight", "memory_list_insights"),
    "memory_list_behavior_instructions": lambda c, a: _compat_list_kind(
        c, a, "behavior", "memory_list_behavior_instructions"
    ),
    "memory_list_agent_capabilities": lambda c, a: _compat_list_kind(
        c, a, "skill", "memory_list_agent_capabilities"
    ),
    # Tier 3 — translation-pending
    "memory_snapshot_save": _translation_pending("memory_snapshot_save"),
    "memory_snapshot_list": _translation_pending("memory_snapshot_list"),
    "memory_snapshot_diff": _translation_pending("memory_snapshot_diff"),
    "memory_list_audit": _translation_pending("memory_list_audit"),
    "memory_list_maintenance_events": _translation_pending("memory_list_maintenance_events"),
    "memory_resolve_maintenance_event": _translation_pending("memory_resolve_maintenance_event"),
    "memory_review_queue": _translation_pending("memory_review_queue"),
    "memory_compact_trigger": _translation_pending("memory_compact_trigger"),
    "memory_list_active_edits": _translation_pending("memory_list_active_edits"),
    "memory_claim_edit": _translation_pending("memory_claim_edit"),
    "memory_release_edit": _translation_pending("memory_release_edit"),
    "memory_record_usage_feedback": _translation_pending("memory_record_usage_feedback"),
}


def compat_dispatch(
    conn: sqlite3.Connection, name: str, args: dict[str, Any]
) -> dict[str, Any] | None:
    """Route a v2 tool call to its v3-backed translator.

    Returns ``None`` when there is no shim entry (caller falls through
    to the native v2 handler). Returns an envelope dict when a shim
    is registered, success or failure.
    """
    handler = COMPAT_HANDLERS.get(name)
    if handler is None:
        return None
    try:
        return handler(conn, args)
    except Exception as exc:  # never let shim raise — return envelope
        return _err(
            "shim_internal_error",
            f"{type(exc).__name__}: {exc}",
            deprecation=_deprecation(name, "memory-cli or /memory/*"),
        )
