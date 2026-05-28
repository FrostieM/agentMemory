"""v3.0.0-final UI action endpoints: reflex CRUD + self-model refresh + recall.

Routes:
  POST /memory/reflex/list          -- list reflex rules in workspace
  POST /memory/reflex/upsert        -- create or update a reflex rule
  POST /memory/reflex/toggle_active -- flip active bit
  POST /memory/reflex/promote       -- set enforcement='block'
  POST /memory/reflex/demote        -- set enforcement='advisory'
  POST /memory/reflex/delete        -- remove rule
  POST /memory/self_model/refresh   -- recompute self-model from current rows
  POST /memory/recall               -- spreading-activation recall over the
                                       Hebbian graph + causal links
  POST /memory/insight/promote      -- manual promote candidate insight to
                                       pinned behavior (review-queue path)

Designed for the v3 dashboard. All routes return JSON envelopes
``{ok, data, error}``. Failure-soft: pre-migration DBs return 200 with
empty results rather than 500.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from agent_memory_lite.api.deps import (
    DbDep,
    SettingsDep,
    ensure_workspace_readable,
    ensure_workspace_writable,
)
from agent_memory_lite.api.workspace_routing import ensure_workspace_matches_db
from agent_memory_lite.cognition.self_model import (
    load_self_model,
    refresh_self_model,
)
from agent_memory_lite.compaction.promote_insight_to_behavior import (
    promote_eligible_insights,
)
from agent_memory_lite.retrieval.recall import recall as recall_fn
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

router = APIRouter(include_in_schema=False)


def _ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def _err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error": {"code": code, "message": message}}


def _ensure_write_routed(conn: Any, workspace_id: str, settings: Any) -> None:
    ensure_workspace_writable(workspace_id, settings)
    ensure_workspace_matches_db(conn, workspace_id, settings)


# ============================================================
# Reflex rules CRUD
# ============================================================


class ListReflexRulesRequest(BaseModel):
    workspace_id: str = Field(min_length=1)


@router.post("/memory/reflex/list")
def list_reflex_rules(
    body: ListReflexRulesRequest, conn: DbDep, settings: SettingsDep
) -> dict[str, Any]:
    ensure_workspace_readable(body.workspace_id, settings)
    try:
        rows = conn.execute(
            """SELECT id, rule_name, trigger_tool, trigger_pattern,
                      precondition_kind, precondition_param_json, enforcement,
                      derived_from_insight_id, active, block_count,
                      advisory_count, last_fired_at, created_at, updated_at
                 FROM reflex_rules
                WHERE workspace_id = ?
                ORDER BY active DESC, (block_count + advisory_count) DESC,
                         updated_at DESC""",
            (body.workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return _ok({"rules": []})
    rules = [
        {
            "id": row["id"],
            "rule_name": row["rule_name"],
            "trigger_tool": row["trigger_tool"],
            "trigger_pattern": row["trigger_pattern"],
            "precondition_kind": row["precondition_kind"],
            "precondition_param_json": row["precondition_param_json"],
            "enforcement": row["enforcement"],
            "derived_from_insight_id": row["derived_from_insight_id"],
            "active": bool(row["active"]),
            "block_count": int(row["block_count"] or 0),
            "advisory_count": int(row["advisory_count"] or 0),
            "last_fired_at": row["last_fired_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]
    return _ok({"rules": rules})


class UpsertReflexRuleRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    id: str | None = None
    rule_name: str = Field(min_length=1, max_length=120)
    trigger_tool: str = Field(min_length=1, max_length=80)
    trigger_pattern: str = ""
    precondition_kind: str = Field(min_length=1)
    precondition_param_json: str = "{}"
    enforcement: str = "advisory"
    active: bool = True


@router.post("/memory/reflex/upsert")
def upsert_reflex_rule(
    body: UpsertReflexRuleRequest, conn: DbDep, settings: SettingsDep
) -> dict[str, Any]:
    _ensure_write_routed(conn, body.workspace_id, settings)
    if body.enforcement not in ("advisory", "block"):
        return _err("validation", "enforcement must be 'advisory' or 'block'")
    now = iso_now()
    rule_id = body.id or new_id(IdKind.AUDIT)
    try:
        existing = conn.execute(
            "SELECT id FROM reflex_rules WHERE workspace_id=? AND id=?",
            (body.workspace_id, rule_id),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE reflex_rules SET rule_name=?, trigger_tool=?,
                          trigger_pattern=?, precondition_kind=?,
                          precondition_param_json=?, enforcement=?,
                          active=?, updated_at=?
                    WHERE id=? AND workspace_id=?""",
                (
                    body.rule_name,
                    body.trigger_tool,
                    body.trigger_pattern,
                    body.precondition_kind,
                    body.precondition_param_json,
                    body.enforcement,
                    1 if body.active else 0,
                    now,
                    rule_id,
                    body.workspace_id,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO reflex_rules
                   (id, workspace_id, rule_name, trigger_tool, trigger_pattern,
                    precondition_kind, precondition_param_json, enforcement,
                    active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rule_id,
                    body.workspace_id,
                    body.rule_name,
                    body.trigger_tool,
                    body.trigger_pattern,
                    body.precondition_kind,
                    body.precondition_param_json,
                    body.enforcement,
                    1 if body.active else 0,
                    now,
                    now,
                ),
            )
        conn.commit()
    except sqlite3.Error as exc:
        return _err("db_error", str(exc))
    return _ok({"id": rule_id})


class ReflexRuleActionRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    id: str = Field(min_length=1)


@router.post("/memory/reflex/toggle_active")
def toggle_active(
    body: ReflexRuleActionRequest, conn: DbDep, settings: SettingsDep
) -> dict[str, Any]:
    _ensure_write_routed(conn, body.workspace_id, settings)
    try:
        conn.execute(
            "UPDATE reflex_rules SET active=1-active, updated_at=? WHERE id=? AND workspace_id=?",
            (iso_now(), body.id, body.workspace_id),
        )
        conn.commit()
    except sqlite3.Error as exc:
        return _err("db_error", str(exc))
    return _ok({"id": body.id})


@router.post("/memory/reflex/promote")
def promote_reflex(
    body: ReflexRuleActionRequest, conn: DbDep, settings: SettingsDep
) -> dict[str, Any]:
    _ensure_write_routed(conn, body.workspace_id, settings)
    try:
        conn.execute(
            "UPDATE reflex_rules SET enforcement='block', updated_at=? "
            "WHERE id=? AND workspace_id=?",
            (iso_now(), body.id, body.workspace_id),
        )
        conn.commit()
    except sqlite3.Error as exc:
        return _err("db_error", str(exc))
    return _ok({"id": body.id, "enforcement": "block"})


@router.post("/memory/reflex/demote")
def demote_reflex(
    body: ReflexRuleActionRequest, conn: DbDep, settings: SettingsDep
) -> dict[str, Any]:
    _ensure_write_routed(conn, body.workspace_id, settings)
    try:
        conn.execute(
            "UPDATE reflex_rules SET enforcement='advisory', updated_at=? "
            "WHERE id=? AND workspace_id=?",
            (iso_now(), body.id, body.workspace_id),
        )
        conn.commit()
    except sqlite3.Error as exc:
        return _err("db_error", str(exc))
    return _ok({"id": body.id, "enforcement": "advisory"})


@router.post("/memory/reflex/delete")
def delete_reflex(
    body: ReflexRuleActionRequest, conn: DbDep, settings: SettingsDep
) -> dict[str, Any]:
    _ensure_write_routed(conn, body.workspace_id, settings)
    try:
        conn.execute(
            "DELETE FROM reflex_rules WHERE id=? AND workspace_id=?",
            (body.id, body.workspace_id),
        )
        conn.commit()
    except sqlite3.Error as exc:
        return _err("db_error", str(exc))
    return _ok({"id": body.id, "deleted": True})


# ============================================================
# Self-model refresh + read
# ============================================================


class SelfModelRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    use_ollama: bool = False


@router.post("/memory/self_model/refresh")
def self_model_refresh(
    body: SelfModelRequest, conn: DbDep, settings: SettingsDep
) -> dict[str, Any]:
    _ensure_write_routed(conn, body.workspace_id, settings)
    try:
        ollama_url = settings.llm_base_url if body.use_ollama else None
        ollama_model = settings.llm_model if body.use_ollama else None
        model = refresh_self_model(
            conn,
            workspace_id=body.workspace_id,
            ollama_base_url=ollama_url,
            ollama_model=ollama_model,
        )
        conn.commit()
    except sqlite3.Error as exc:
        return _err("db_error", str(exc))
    if model is None:
        return _err("not_available", "self_model table missing or refresh returned None")
    return _ok(
        {
            "identity_text": model.identity_text,
            "word_count": len(model.identity_text.split()),
            "refreshed_via": model.refreshed_via,
            "coverage_score": round(model.coverage_score, 3),
            "invariants_count": len(model.invariants),
            "uncertainties_count": len(model.uncertainties),
        }
    )


@router.post("/memory/self_model/get")
def self_model_get(
    body: ListReflexRulesRequest, conn: DbDep, settings: SettingsDep
) -> dict[str, Any]:
    """Read-only fetch (reuses ListReflexRulesRequest = just {workspace_id})."""
    ensure_workspace_readable(body.workspace_id, settings)
    model = load_self_model(conn, workspace_id=body.workspace_id)
    if model is None:
        return _ok(None)
    return _ok(
        {
            "identity_text": model.identity_text,
            "word_count": len(model.identity_text.split()),
            "refreshed_via": model.refreshed_via,
            "coverage_score": round(model.coverage_score, 3),
            "invariants": model.invariants,
            "uncertainties": model.uncertainties,
            "source_decision_ids": model.source_decision_ids,
        }
    )


# ============================================================
# Recall (interactive)
# ============================================================


class RecallRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    topic: str = Field(min_length=1, max_length=500)
    depth: int = Field(default=2, ge=1, le=3)
    outcome_floor: float = Field(default=-1.0, ge=-1.0, le=1.0)
    limit: int = Field(default=10, ge=1, le=50)
    kinds: list[str] | None = None
    as_of: str | None = None


@router.post("/memory/recall")
def recall_route(body: RecallRequest, conn: DbDep, settings: SettingsDep) -> dict[str, Any]:
    ensure_workspace_readable(body.workspace_id, settings)
    try:
        hits = recall_fn(
            conn,
            workspace_id=body.workspace_id,
            topic=body.topic,
            depth=body.depth,
            outcome_floor=body.outcome_floor,
            kinds=body.kinds,
            as_of=body.as_of,
            limit=body.limit,
        )
    except sqlite3.OperationalError:
        return _ok({"hits": [], "topic": body.topic})
    return _ok(
        {
            "topic": body.topic,
            "depth": body.depth,
            "outcome_floor": body.outcome_floor,
            "hits": [
                {
                    "kind": h.kind,
                    "id": h.object_id,
                    "activation": round(h.activation, 4),
                    "hops": h.hops,
                    "outcome_score": round(h.outcome_score, 4),
                    "score": round(h.score, 4),
                    "projection": h.projection,
                    "causal_links": h.causal_links,
                }
                for h in hits
            ],
        }
    )


# ============================================================
# Insight promote (manual review-queue path)
# ============================================================


class PromoteInsightsRequest(BaseModel):
    workspace_id: str = Field(min_length=1)


@router.post("/memory/insight/promote_eligible")
def insight_promote(
    body: PromoteInsightsRequest, conn: DbDep, settings: SettingsDep
) -> dict[str, Any]:
    _ensure_write_routed(conn, body.workspace_id, settings)
    try:
        stats = promote_eligible_insights(conn, workspace_id=body.workspace_id)
        conn.commit()
    except sqlite3.Error as exc:
        return _err("db_error", str(exc))
    return _ok(
        {
            "inspected": stats.inspected,
            "promoted": stats.promoted,
            "skipped": stats.skipped,
        }
    )
