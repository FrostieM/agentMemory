"""Handlers for theory write + theory evidence + theory list."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.ingestion._write_helpers import (
    capability_suggestion_dicts,
    resolve_source_episode_id,
)
from agent_memory_lite.ingestion.theory_writer import add_theory_evidence, write_theory
from agent_memory_lite.mcp.stdio_guards import _with_workspace, _workspace_from_args
from agent_memory_lite.mcp.stdio_http import _http_write
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.models.theories import TheoryEvidenceIn, TheoryIn
from agent_memory_lite.repositories.theories_repo import list_evidence_for_theory, list_theories


def _handle_write_theory(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write("/memory/write_theory", payload, log_label="write_theory")
    if delegated is not None:
        return delegated

    # Local-fallback path: apply Move 1 (auto-thread) and Move 4
    # (capability_suggestions) so MCP-only deployments stay aligned
    # with the HTTP route. ``allow_orphan`` is consumed before
    # building TheoryIn (domain model has no such field).
    workspace_id = str(payload.get("workspace_id") or _runtime.settings.workspace_id)
    allow_orphan = bool(payload.pop("allow_orphan", False))
    conn = _runtime.db_for(workspace_id)
    payload["source_episode_id"] = resolve_source_episode_id(
        conn,
        workspace_id=workspace_id,
        explicit=payload.get("source_episode_id"),
        allow_orphan=allow_orphan,
        settings=_runtime.settings,
    )
    theory = write_theory(conn, TheoryIn(**payload))
    return {
        "theory_id": theory.id,
        "status": theory.status.value,
        "confidence": theory.confidence,
        "importance": theory.importance,
        "evidence_count": theory.evidence_count,
        "evidence_strength": theory.evidence_strength,
        "source_episode_id": theory.source_episode_id,
        "capability_suggestions": capability_suggestion_dicts(
            conn,
            workspace_id=workspace_id,
            title=str(payload.get("title") or ""),
            text=str(payload.get("claim") or ""),
            rationale=payload.get("mechanism"),
        ),
    }


def _handle_add_theory_evidence(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write(
        "/memory/add_theory_evidence",
        payload,
        log_label="add_theory_evidence",
    )
    if delegated is not None:
        return delegated

    evidence = add_theory_evidence(
        _runtime.db_for(str(payload["workspace_id"])), TheoryEvidenceIn(**payload)
    )
    return {
        "evidence_id": evidence.id,
        "theory_id": evidence.theory_id,
        "kind": evidence.kind.value,
        "observed_at": evidence.observed_at,
    }


def _handle_list_theories(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args, intent="read")
    include_evidence = bool(args.get("include_evidence", False))
    evidence_limit = int(args.get("evidence_limit", 3))
    db = _runtime.db_for(workspace_id)
    theories = list_theories(
        db,
        workspace_id=workspace_id,
        query=args.get("query"),
        limit=int(args.get("limit", 20)),
        include_archived=bool(args.get("include_archived", False)),
    )
    return {
        "theories": [
            {
                "theory_id": theory.id,
                "title": theory.title,
                "domain": theory.domain,
                "claim": theory.claim,
                "validation_criteria": theory.validation_criteria,
                "dependent_decision_ids": theory.dependent_decision_ids,
                "status": theory.status.value,
                "confidence": theory.confidence,
                "importance": theory.importance,
                "evidence_count": theory.evidence_count,
                "evidence_strength": theory.evidence_strength,
                "tags": theory.tags,
                "evidence": [
                    {
                        "evidence_id": evidence.id,
                        "kind": evidence.kind.value,
                        "summary": evidence.summary,
                        "confidence": evidence.confidence,
                        "observed_at": evidence.observed_at,
                    }
                    for evidence in (
                        list_evidence_for_theory(db, theory.id, limit=evidence_limit)
                        if include_evidence
                        else []
                    )
                ],
            }
            for theory in theories
        ],
    }
