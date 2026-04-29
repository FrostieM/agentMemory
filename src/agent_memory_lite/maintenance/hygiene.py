"""Detailed memory hygiene report.

Integrity checks answer whether retrieval is mechanically consistent. Hygiene
checks answer whether the memory remains scientifically useful: open candidates
must be triaged, theories must be testable, experiments must close, insights
must be linked, and important objects should be influenced by roles/skills.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_memory_lite.utils.time import iso_now

_TOKEN_RE = re.compile(r"\w+(?:[-.]\w+)*", re.UNICODE)
_STOPWORDS = {
    "about",
    "active",
    "across",
    "also",
    "and",
    "any",
    "are",
    "after",
    "agent",
    "app",
    "because",
    "behavior",
    "before",
    "between",
    "bot",
    "but",
    "call",
    "can",
    "check",
    "clear",
    "could",
    "current",
    "decision",
    "does",
    "done",
    "during",
    "event",
    "exact",
    "existing",
    "explicit",
    "explicitly",
    "file",
    "files",
    "for",
    "found",
    "flow",
    "from",
    "full",
    "general",
    "has",
    "have",
    "important",
    "includes",
    "instead",
    "into",
    "issue",
    "keep",
    "labels",
    "least",
    "make",
    "memory",
    "missing",
    "must",
    "new",
    "not",
    "object",
    "old",
    "only",
    "one",
    "over",
    "pass",
    "path",
    "phase",
    "plus",
    "rather",
    "real",
    "remains",
    "required",
    "research",
    "responsive",
    "row",
    "rows",
    "safety",
    "run",
    "runs",
    "same",
    "show",
    "shows",
    "should",
    "small",
    "state",
    "status",
    "still",
    "that",
    "the",
    "their",
    "there",
    "this",
    "too",
    "tool",
    "tools",
    "under",
    "used",
    "using",
    "via",
    "wait",
    "waiting",
    "when",
    "where",
    "while",
    "with",
    "without",
    "work",
    "would",
}


@dataclass(frozen=True, slots=True)
class HygieneFinding:
    kind: str
    severity: str
    target_type: str
    target_id: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "summary": self.summary,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class HygieneReport:
    status: str
    workspace_id: str
    generated_at: str
    counts: dict[str, int]
    findings: list[HygieneFinding]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "workspace_id": self.workspace_id,
            "generated_at": self.generated_at,
            "counts": self.counts,
            "findings": [finding.to_dict() for finding in self.findings],
        }


@dataclass(frozen=True, slots=True)
class _CapabilityCandidate:
    capability_type: str
    capability_id: str
    capability_name: str
    text: str
    confidence: float


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'virtual table')",
        (table,),
    ).fetchone()
    return row is not None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _json_len(raw: str | None) -> int:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return 0
    if isinstance(data, list | dict):
        return len(data)
    return 0


def _json_text(raw: str | None) -> str:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return ""

    def _flatten(value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str | int | float | bool):
            return [str(value)]
        if isinstance(value, list):
            items: list[str] = []
            for item in value:
                items.extend(_flatten(item))
            return items
        if isinstance(value, dict):
            items = []
            for key, item in value.items():
                items.append(str(key))
                items.extend(_flatten(item))
            return items
        return [str(value)]

    return " ".join(_flatten(data))


def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    return {
        token.lower()
        for token in _TOKEN_RE.findall(text)
        if len(token) > 2 and token.lower() not in _STOPWORDS
    }


def _capability_text(row: sqlite3.Row, fields: list[str]) -> str:
    parts = [str(row["name"])]
    for field_name in fields:
        raw = row[field_name]
        if field_name.endswith("_json"):
            parts.append(_json_text(str(raw) if raw is not None else None))
        elif raw:
            parts.append(str(raw))
    return " ".join(parts)


def _load_active_capabilities(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
) -> list[_CapabilityCandidate]:
    candidates: list[_CapabilityCandidate] = []
    if _table_exists(conn, "agent_roles"):
        rows = conn.execute(
            """
            SELECT id, name, purpose, responsibilities_json, boundaries_json,
                   handoff_triggers_json, tools_json, confidence
            FROM agent_roles
            WHERE workspace_id = ? AND active = 1
            """,
            (workspace_id,),
        ).fetchall()
        candidates.extend(
            _CapabilityCandidate(
                capability_type="role",
                capability_id=str(row["id"]),
                capability_name=str(row["name"]),
                text=_capability_text(
                    row,
                    [
                        "purpose",
                        "responsibilities_json",
                        "boundaries_json",
                        "handoff_triggers_json",
                        "tools_json",
                    ],
                ),
                confidence=float(row["confidence"]),
            )
            for row in rows
        )
    if _table_exists(conn, "agent_skills"):
        rows = conn.execute(
            """
            SELECT id, name, summary, when_to_use_json, inputs_json, outputs_json,
                   tools_json, related_roles_json, confidence
            FROM agent_skills
            WHERE workspace_id = ? AND active = 1
            """,
            (workspace_id,),
        ).fetchall()
        candidates.extend(
            _CapabilityCandidate(
                capability_type="skill",
                capability_id=str(row["id"]),
                capability_name=str(row["name"]),
                text=_capability_text(
                    row,
                    [
                        "summary",
                        "when_to_use_json",
                        "inputs_json",
                        "outputs_json",
                        "tools_json",
                        "related_roles_json",
                    ],
                ),
                confidence=float(row["confidence"]),
            )
            for row in rows
        )
    if _table_exists(conn, "agent_playbooks"):
        rows = conn.execute(
            """
            SELECT id, name, goal, triggers_json, steps_json, success_criteria_json,
                   required_skills_json, confidence
            FROM agent_playbooks
            WHERE workspace_id = ? AND active = 1
            """,
            (workspace_id,),
        ).fetchall()
        candidates.extend(
            _CapabilityCandidate(
                capability_type="playbook",
                capability_id=str(row["id"]),
                capability_name=str(row["name"]),
                text=_capability_text(
                    row,
                    [
                        "goal",
                        "triggers_json",
                        "steps_json",
                        "success_criteria_json",
                        "required_skills_json",
                    ],
                ),
                confidence=float(row["confidence"]),
            )
            for row in rows
        )
    return candidates


def _suggested_relation(target_type: str, capability_type: str) -> str:
    relation_map = {
        ("theory", "role"): "critique_lens",
        ("theory", "skill"): "evidence_method",
        ("theory", "playbook"): "validation_playbook",
        ("experiment", "role"): "reviewer",
        ("experiment", "skill"): "required_skill",
        ("experiment", "playbook"): "validation_playbook",
        ("decision", "role"): "implementation_role",
        ("decision", "skill"): "method",
        ("decision", "playbook"): "validation_playbook",
    }
    return relation_map.get((target_type, capability_type), "method")


def _capability_type_bonus(target_type: str, capability_type: str) -> float:
    bonus_map = {
        ("theory", "role"): 0.15,
        ("theory", "skill"): 0.25,
        ("theory", "playbook"): 0.25,
        ("experiment", "role"): 0.15,
        ("experiment", "skill"): 0.25,
        ("experiment", "playbook"): 0.30,
        ("decision", "role"): 0.35,
        ("decision", "skill"): 0.0,
        ("decision", "playbook"): 0.25,
    }
    return bonus_map.get((target_type, capability_type), 0.0)


def _suggest_capability_links(
    capabilities: list[_CapabilityCandidate],
    *,
    workspace_id: str,
    target_type: str,
    target_id: str,
    target_label: str,
    target_text: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    target_terms = _tokens(f"{target_label} {target_text}")
    capability_terms: list[tuple[_CapabilityCandidate, set[str]]] = [
        (capability, _tokens(capability.text)) for capability in capabilities
    ]
    term_counts: dict[str, int] = {}
    for _, terms in capability_terms:
        for term in terms:
            term_counts[term] = term_counts.get(term, 0) + 1

    ranked: list[tuple[float, float, str, _CapabilityCandidate, list[str]]] = []
    match_scores: dict[str, float] = {}
    common_term_limit = max(2, len(capability_terms) // 3)
    for capability, terms in capability_terms:
        matched_terms = target_terms & terms
        if not matched_terms:
            continue
        rare_matches = [term for term in matched_terms if term_counts[term] <= common_term_limit]
        weighted_match_score = sum(1.0 / term_counts[term] for term in matched_terms)
        if not rare_matches and weighted_match_score < 1.2:
            continue
        ordered_terms = sorted(
            matched_terms,
            key=lambda term: (1.0 / term_counts[term], len(term), term),
            reverse=True,
        )
        score = (
            weighted_match_score
            + capability.confidence
            + _capability_type_bonus(target_type, capability.capability_type)
        )
        match_scores[capability.capability_id] = weighted_match_score
        ranked.append(
            (score, capability.confidence, capability.capability_name, capability, ordered_terms)
        )
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    suggestions: list[dict[str, Any]] = []
    for _, confidence, _, capability, ordered_matches in ranked[:limit]:
        relation = _suggested_relation(target_type, capability.capability_type)
        weighted_score = match_scores[capability.capability_id]
        strength = min(0.9, round(0.45 + 0.06 * weighted_score + 0.10 * confidence, 2))
        matched_preview = ordered_matches[:8]
        suggestions.append(
            {
                "workspace_id": workspace_id,
                "target_type": target_type,
                "target_id": target_id,
                "capability_type": capability.capability_type,
                "capability_id": capability.capability_id,
                "capability_name": capability.capability_name,
                "relation": relation,
                "strength": strength,
                "match_score": round(weighted_score, 3),
                "matched_terms": matched_preview,
                "rationale": (
                    "Suggested by hygiene because the target and capability share terms: "
                    f"{', '.join(matched_preview)}."
                ),
            }
        )
    return suggestions


def _find_stale_candidates(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    stale_days: int,
) -> list[HygieneFinding]:
    if not _table_exists(conn, "memory_candidates"):
        return []
    cutoff = datetime.now(UTC) - timedelta(days=stale_days)
    rows = conn.execute(
        """
        SELECT id, kind, subject, updated_at
        FROM memory_candidates
        WHERE workspace_id = ? AND status = 'new'
        ORDER BY updated_at
        """,
        (workspace_id,),
    ).fetchall()
    findings: list[HygieneFinding] = []
    for row in rows:
        updated = _parse_iso(str(row["updated_at"]))
        if updated is None or updated >= cutoff:
            continue
        findings.append(
            HygieneFinding(
                kind="stale_candidate",
                severity="warning",
                target_type="memory_candidate",
                target_id=str(row["id"]),
                summary="Candidate remained new past the review window.",
                details={
                    "candidate_kind": row["kind"],
                    "subject": row["subject"],
                    "updated_at": row["updated_at"],
                    "stale_days": stale_days,
                },
            )
        )
    return findings


def _find_theory_gaps(conn: sqlite3.Connection, *, workspace_id: str) -> list[HygieneFinding]:
    if not _table_exists(conn, "theories"):
        return []
    rows = conn.execute(
        """
        SELECT id, title, status, validation_criteria_json, experiment_plan,
               evidence_count, importance
        FROM theories
        WHERE workspace_id = ?
          AND status IN ('proposed', 'testing', 'supported', 'validated', 'rejected')
        ORDER BY importance DESC, updated_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    findings: list[HygieneFinding] = []
    for row in rows:
        theory_id = str(row["id"])
        title = str(row["title"])
        status = str(row["status"])
        validation_count = _json_len(row["validation_criteria_json"])
        if status != "rejected" and validation_count == 0 and not row["experiment_plan"]:
            findings.append(
                HygieneFinding(
                    kind="theory_without_validation",
                    severity="warning",
                    target_type="theory",
                    target_id=theory_id,
                    summary="Active theory has no validation criteria or experiment plan.",
                    details={"title": title, "status": status, "importance": row["importance"]},
                )
            )
        if int(row["evidence_count"]) == 0:
            findings.append(
                HygieneFinding(
                    kind="theory_without_evidence",
                    severity="warning",
                    target_type="theory",
                    target_id=theory_id,
                    summary="Theory has no attached evidence yet.",
                    details={"title": title, "status": status, "importance": row["importance"]},
                )
            )
    return findings


def _find_experiment_gaps(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    stale_days: int,
) -> list[HygieneFinding]:
    if not _table_exists(conn, "research_experiments"):
        return []
    cutoff = datetime.now(UTC) - timedelta(days=stale_days)
    rows = conn.execute(
        """
        SELECT id, title, status, priority, due_at, updated_at, success_criteria_json
        FROM research_experiments
        WHERE workspace_id = ? AND status IN ('planned', 'running', 'blocked')
        ORDER BY priority DESC, updated_at
        """,
        (workspace_id,),
    ).fetchall()
    findings: list[HygieneFinding] = []
    now = datetime.now(UTC)
    for row in rows:
        experiment_id = str(row["id"])
        title = str(row["title"])
        due_at = _parse_iso(row["due_at"])
        updated_at = _parse_iso(str(row["updated_at"]))
        if due_at is not None and due_at < now:
            findings.append(
                HygieneFinding(
                    kind="overdue_experiment",
                    severity="warning",
                    target_type="experiment",
                    target_id=experiment_id,
                    summary="Open experiment is past due.",
                    details={"title": title, "status": row["status"], "due_at": row["due_at"]},
                )
            )
        if updated_at is not None and updated_at < cutoff:
            findings.append(
                HygieneFinding(
                    kind="stale_open_experiment",
                    severity="warning",
                    target_type="experiment",
                    target_id=experiment_id,
                    summary="Open experiment has not been updated within the hygiene window.",
                    details={
                        "title": title,
                        "status": row["status"],
                        "updated_at": row["updated_at"],
                        "stale_days": stale_days,
                    },
                )
            )
        if _json_len(row["success_criteria_json"]) == 0:
            findings.append(
                HygieneFinding(
                    kind="experiment_without_success_criteria",
                    severity="warning",
                    target_type="experiment",
                    target_id=experiment_id,
                    summary="Open experiment has no explicit success criteria.",
                    details={"title": title, "status": row["status"], "priority": row["priority"]},
                )
            )
    return findings


def _find_insight_gaps(conn: sqlite3.Connection, *, workspace_id: str) -> list[HygieneFinding]:
    if not _table_exists(conn, "research_insights"):
        return []
    rows = conn.execute(
        """
        SELECT id, insight_type, summary, status, target_type, target_id
        FROM research_insights
        WHERE workspace_id = ? AND status IN ('new', 'accepted')
          AND (COALESCE(target_type, '') = '' OR COALESCE(target_id, '') = '')
        ORDER BY updated_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    return [
        HygieneFinding(
            kind="unlinked_insight",
            severity="warning",
            target_type="research_insight",
            target_id=str(row["id"]),
            summary="Active insight is not linked to a target object.",
            details={
                "insight_type": row["insight_type"],
                "status": row["status"],
                "summary": row["summary"],
            },
        )
        for row in rows
    ]


def _find_decision_gaps(conn: sqlite3.Connection, *, workspace_id: str) -> list[HygieneFinding]:
    if not _table_exists(conn, "decisions"):
        return []
    rows = conn.execute(
        """
        SELECT id, title, rationale, source_episode_id, importance
        FROM decisions
        WHERE workspace_id = ? AND status = 'active' AND importance >= 0.8
          AND COALESCE(rationale, '') = ''
          AND COALESCE(source_episode_id, '') = ''
        ORDER BY importance DESC, updated_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    return [
        HygieneFinding(
            kind="weak_decision_provenance",
            severity="warning",
            target_type="decision",
            target_id=str(row["id"]),
            summary="Important active decision lacks both rationale and source episode.",
            details={
                "title": row["title"],
                "has_rationale": bool(row["rationale"]),
                "has_source_episode": bool(row["source_episode_id"]),
                "importance": row["importance"],
            },
        )
        for row in rows
    ]


def _find_unlinked_capability_targets(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    importance_threshold: float,
) -> list[HygieneFinding]:
    if not _table_exists(conn, "capability_links"):
        return []
    capabilities = _load_active_capabilities(conn, workspace_id=workspace_id)
    specs = [
        (
            "theory",
            "theories",
            """
            id,
            title AS label,
            importance,
            status,
            COALESCE(title, '') || ' ' ||
            COALESCE(domain, '') || ' ' ||
            COALESCE(claim, '') || ' ' ||
            COALESCE(mechanism, '') || ' ' ||
            COALESCE(experiment_plan, '') || ' ' ||
            COALESCE(predictions_json, '') || ' ' ||
            COALESCE(validation_criteria_json, '') || ' ' ||
            COALESCE(tags_json, '') AS search_text
            """,
            "importance >= ? AND status IN ('proposed', 'testing', 'supported', 'validated', 'rejected')",
            "importance",
        ),
        (
            "experiment",
            "research_experiments",
            """
            id,
            title AS label,
            priority AS importance,
            status,
            COALESCE(title, '') || ' ' ||
            COALESCE(hypothesis, '') || ' ' ||
            COALESCE(cohort_definition, '') || ' ' ||
            COALESCE(success_criteria_json, '') || ' ' ||
            COALESCE(command, '') || ' ' ||
            COALESCE(owner, '') || ' ' ||
            COALESCE(metadata_json, '') AS search_text
            """,
            "priority >= ? AND status IN ('planned', 'running', 'blocked')",
            "priority",
        ),
        (
            "decision",
            "decisions",
            """
            id,
            title AS label,
            importance,
            status,
            COALESCE(title, '') || ' ' ||
            COALESCE(decision_text, '') || ' ' ||
            COALESCE(rationale, '') AS search_text
            """,
            "importance >= ? AND status = 'active'",
            "importance",
        ),
    ]
    findings: list[HygieneFinding] = []
    for target_type, table, columns, where_sql, score_field in specs:
        if not _table_exists(conn, table):
            continue
        rows = conn.execute(
            f"""
            SELECT {columns}
            FROM {table} t
            WHERE t.workspace_id = ?
              AND {where_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM capability_links l
                WHERE l.workspace_id = t.workspace_id
                  AND l.target_type = ?
                  AND l.target_id = t.id
              )
            ORDER BY {score_field} DESC
            """,
            (workspace_id, importance_threshold, target_type),
        ).fetchall()
        for row in rows:
            target_id = str(row["id"])
            label = str(row["label"])
            search_text = str(row["search_text"])
            findings.append(
                HygieneFinding(
                    kind="missing_capability_link",
                    severity="warning",
                    target_type=target_type,
                    target_id=target_id,
                    summary="Important object has no role/skill/playbook influence link.",
                    details={
                        "label": label,
                        "status": row["status"],
                        "importance": row["importance"],
                        "suggested_capability_links": _suggest_capability_links(
                            capabilities,
                            workspace_id=workspace_id,
                            target_type=target_type,
                            target_id=target_id,
                            target_label=label,
                            target_text=search_text,
                        ),
                    },
                )
            )
    return findings


def _count_by_kind(findings: list[HygieneFinding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.kind] = counts.get(finding.kind, 0) + 1
    return counts


def run_hygiene_report(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    candidate_stale_days: int = 14,
    experiment_stale_days: int = 30,
    importance_threshold: float = 0.8,
) -> HygieneReport:
    findings: list[HygieneFinding] = []
    findings.extend(
        _find_stale_candidates(conn, workspace_id=workspace_id, stale_days=candidate_stale_days)
    )
    findings.extend(_find_theory_gaps(conn, workspace_id=workspace_id))
    findings.extend(
        _find_experiment_gaps(
            conn,
            workspace_id=workspace_id,
            stale_days=experiment_stale_days,
        )
    )
    findings.extend(_find_insight_gaps(conn, workspace_id=workspace_id))
    findings.extend(_find_decision_gaps(conn, workspace_id=workspace_id))
    findings.extend(
        _find_unlinked_capability_targets(
            conn,
            workspace_id=workspace_id,
            importance_threshold=importance_threshold,
        )
    )
    counts = _count_by_kind(findings)
    counts["total_findings"] = len(findings)
    status = "warning" if findings else "ok"
    return HygieneReport(
        status=status,
        workspace_id=workspace_id,
        generated_at=iso_now(),
        counts=counts,
        findings=findings,
    )
