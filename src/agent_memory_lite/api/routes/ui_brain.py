"""v3.0.0-final brain-aware UI data endpoint.

Single endpoint that returns every signal the v3 dashboard needs:

  * outcome_distribution -- per-kind positive/negative/neutral counts +
    mean outcome score (so the UI can draw donut charts and overall mood)
  * self_model            -- identity narrative + invariants + uncertainties
  * watch_outs            -- top-N rows with outcome <= -0.3 (cross-kind)
  * recent_insights       -- top-N candidate insights with confidence band
  * reflex_rules_summary  -- active count by enforcement + top fire counters
  * causal_links_summary  -- count per relation kind
  * hebbian_summary       -- soft_edge count by edge_kind

Designed for one round-trip from the dashboard. Each section is
defensively wrapped in try/except so a single missing column (pre-
migration DB) returns an empty section instead of failing the whole
call.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Query

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable

router = APIRouter(include_in_schema=False)

_OUTCOME_KINDS = (
    ("decisions", "decision"),
    ("theories", "theory"),
    ("behaviors", "behavior"),
    ("skills", "skill"),
    ("insights", "insight"),
    ("chunks", "chunk"),
)


def _outcome_distribution(conn: sqlite3.Connection, *, workspace_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for table, label in _OUTCOME_KINDS:
        try:
            row = conn.execute(
                f"""SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN outcome_score > 0.0 THEN 1 ELSE 0 END) AS positive,
                    SUM(CASE WHEN outcome_score < 0.0 THEN 1 ELSE 0 END) AS negative,
                    SUM(CASE WHEN outcome_score = 0.0 THEN 1 ELSE 0 END) AS neutral,
                    AVG(outcome_score) AS mean
                  FROM {table} WHERE workspace_id = ?""",
                (workspace_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            continue
        if row is None:
            continue
        out[label] = {
            "total": int(row["total"] or 0),
            "positive": int(row["positive"] or 0),
            "negative": int(row["negative"] or 0),
            "neutral": int(row["neutral"] or 0),
            "mean": round(float(row["mean"] or 0.0), 3),
        }
    return out


def _self_model(conn: sqlite3.Connection, *, workspace_id: str) -> dict[str, Any] | None:
    try:
        row = conn.execute(
            "SELECT identity_text, invariants_json, uncertainties_json, "
            "coverage_score, refreshed_via, updated_at "
            "FROM self_model WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    try:
        invariants = json.loads(row["invariants_json"] or "[]")
        uncertainties = json.loads(row["uncertainties_json"] or "[]")
    except (TypeError, ValueError):
        invariants, uncertainties = [], []
    return {
        "identity_text": row["identity_text"],
        "invariants_count": len(invariants) if isinstance(invariants, list) else 0,
        "uncertainties_count": len(uncertainties) if isinstance(uncertainties, list) else 0,
        "invariants": invariants[:5] if isinstance(invariants, list) else [],
        "uncertainties": uncertainties[:5] if isinstance(uncertainties, list) else [],
        "coverage_score": round(float(row["coverage_score"] or 0.0), 3),
        "refreshed_via": row["refreshed_via"],
        "updated_at": row["updated_at"],
        "word_count": len((row["identity_text"] or "").split()),
    }


def _watch_outs(
    conn: sqlite3.Connection, *, workspace_id: str, limit: int = 8
) -> list[dict[str, Any]]:
    sql = """
      SELECT id, 'decision' AS kind, COALESCE(gist, title, '') AS gist,
             outcome_score, status
        FROM decisions
       WHERE workspace_id = ? AND COALESCE(outcome_score, 0.0) <= -0.3
      UNION ALL
      SELECT id, 'theory' AS kind, COALESCE(gist, title, claim, '') AS gist,
             outcome_score, status
        FROM theories
       WHERE workspace_id = ? AND COALESCE(outcome_score, 0.0) <= -0.3
      UNION ALL
      SELECT id, 'behavior' AS kind, COALESCE(rule_one_line, name, '') AS gist,
             outcome_score, CAST(active AS TEXT) AS status
        FROM behaviors
       WHERE workspace_id = ? AND COALESCE(outcome_score, 0.0) <= -0.3
      UNION ALL
      SELECT id, 'insight' AS kind, COALESCE(gist, summary, '') AS gist,
             outcome_score, status
        FROM insights
       WHERE workspace_id = ? AND COALESCE(outcome_score, 0.0) <= -0.3
      ORDER BY outcome_score ASC LIMIT ?
    """
    try:
        rows = conn.execute(
            sql, (workspace_id, workspace_id, workspace_id, workspace_id, limit)
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {
            "id": row["id"],
            "kind": row["kind"],
            "gist": (row["gist"] or "")[:120],
            "outcome_score": round(float(row["outcome_score"] or 0.0), 3),
            "status": row["status"],
        }
        for row in rows
    ]


def _recent_insights(
    conn: sqlite3.Connection, *, workspace_id: str, limit: int = 5
) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """SELECT id, insight_type, summary, gist, confidence, status,
                      surface_count, updated_at, outcome_score
                 FROM insights
                WHERE workspace_id = ? AND status = 'candidate'
                  AND COALESCE(outcome_score, 0.0) >= 0
                ORDER BY updated_at DESC LIMIT ?""",
            (workspace_id, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {
            "id": row["id"],
            "insight_type": row["insight_type"],
            "summary": (row["summary"] or "")[:140],
            "gist": (row["gist"] or "")[:120],
            "confidence": round(float(row["confidence"] or 0.0), 3),
            "surface_count": int(row["surface_count"] or 0),
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def _reflex_summary(conn: sqlite3.Connection, *, workspace_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "active": 0,
        "by_enforcement": {},
        "total_block_fires": 0,
        "total_advisory_fires": 0,
        "top_rules": [],
    }
    try:
        rows = conn.execute(
            """SELECT enforcement, COUNT(*) AS n,
                      SUM(block_count) AS bf, SUM(advisory_count) AS af
                 FROM reflex_rules
                WHERE workspace_id = ? AND active = 1
                GROUP BY enforcement""",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for row in rows:
        out["active"] += int(row["n"])
        out["by_enforcement"][row["enforcement"]] = int(row["n"])
        out["total_block_fires"] += int(row["bf"] or 0)
        out["total_advisory_fires"] += int(row["af"] or 0)
    try:
        top_rows = conn.execute(
            """SELECT id, rule_name, trigger_tool, precondition_kind, enforcement,
                      block_count, advisory_count, active, last_fired_at
                 FROM reflex_rules WHERE workspace_id = ?
                ORDER BY (block_count + advisory_count) DESC LIMIT 5""",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    out["top_rules"] = [
        {
            "id": row["id"],
            "rule_name": row["rule_name"],
            "trigger_tool": row["trigger_tool"],
            "precondition_kind": row["precondition_kind"],
            "enforcement": row["enforcement"],
            "block_count": int(row["block_count"] or 0),
            "advisory_count": int(row["advisory_count"] or 0),
            "active": bool(row["active"]),
            "last_fired_at": row["last_fired_at"],
        }
        for row in top_rows
    ]
    return out


def _causal_summary(conn: sqlite3.Connection, *, workspace_id: str) -> dict[str, int]:
    try:
        rows = conn.execute(
            "SELECT relation, COUNT(*) AS n FROM causal_links "
            "WHERE workspace_id = ? GROUP BY relation",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {row["relation"]: int(row["n"]) for row in rows}


def _hebbian_summary(conn: sqlite3.Connection, *, workspace_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {"edges_by_kind": {}, "coactivation_rows": 0}
    try:
        edge_rows = conn.execute(
            "SELECT edge_kind, COUNT(*) AS n, AVG(weight) AS w "
            "FROM soft_edges WHERE workspace_id = ? GROUP BY edge_kind",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for row in edge_rows:
        out["edges_by_kind"][row["edge_kind"]] = {
            "count": int(row["n"]),
            "avg_weight": round(float(row["w"] or 0.0), 3),
        }
    try:
        coact = conn.execute(
            "SELECT COUNT(*) FROM retrieval_coactivation WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
        out["coactivation_rows"] = int(coact[0] or 0) if coact else 0
    except sqlite3.OperationalError:
        pass
    return out


@router.get("/memory/ui/brain_state")
def brain_state(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """One-shot v3.0.0-final brain snapshot for the dashboard."""
    ws = workspace_id or settings.workspace_id
    ensure_workspace_readable(ws, settings)
    return {
        "workspace_id": ws,
        "outcome_distribution": _outcome_distribution(conn, workspace_id=ws),
        "self_model": _self_model(conn, workspace_id=ws),
        "watch_outs": _watch_outs(conn, workspace_id=ws),
        "recent_insights": _recent_insights(conn, workspace_id=ws),
        "reflex_summary": _reflex_summary(conn, workspace_id=ws),
        "causal_summary": _causal_summary(conn, workspace_id=ws),
        "hebbian_summary": _hebbian_summary(conn, workspace_id=ws),
    }
