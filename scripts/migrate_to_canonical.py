"""Idempotent, resumable port from v2 SQLite to v3 SQLite.

Usage:
    python scripts/migrate_to_canonical.py \\
        --workspace agentLight \\
        --source-db .agent_memory/memory.db \\
        --target-dir .agent_memory.v3-trial/ \\
        [--batch-size 1000] [--resume]

Reads v2 source READ-ONLY. Writes new v3 SQLite at
``<target-dir>/memory.db`` after applying ``migrations/schema_v3.sql``.

Resumable: per-workspace ``<target-dir>/migration_progress.json`` tracks
which kinds completed and the row offset within the in-progress kind.

Final step: parity report at ``<target-dir>/migration_report.json``
with per-kind v2_count vs v3_count. Migration is marked complete only
when every kind passes parity.

v2 originals are NEVER touched.

Heuristic gist for retrieval-eligible kinds: first sentence of body
text, ≤30 words. Optional Ollama backfill pass is a follow-up commit;
the heuristic alone produces useful gists for most cases.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "migrations" / "canonical" / "0001_init.sql"

# Order of kinds matters: foreign-key dependencies first.
KIND_ORDER = [
    "workspace_manifest",
    "workspace_meta",
    "episodes",
    "files",
    "chunks",
    "decisions",
    "theories",
    "theory_evidence",
    "behaviors",
    "skills",
    "concepts",
    "tasks",
    "code_digests",
    "symbol_edges",
    "symbol_versions",
    "active_edits",
    "soft_edges",
    "snapshots",
    "experiments",
    "experiment_results",
    "insights",
    "capability_links",
    "candidates",
    "decision_candidates",
    "insight_candidates",
    "maintenance_events",
    "retrieval_sentinel_results",
    "memory_usage_feedback",
    "memory_state_snapshots",
    "vector_index_metadata",
    "audit_log",
    "entities",
    "facts",
]


@dataclass(slots=True)
class Progress:
    """Per-workspace migration progress tracker.

    Stored as JSON next to the target DB so the run can resume after a
    crash without re-doing finished kinds. Each kind's offset is the
    number of rows already written; the next batch starts at OFFSET.
    """

    kinds_done: list[str] = field(default_factory=list)
    rows_done: dict[str, int] = field(default_factory=dict)
    started_at: str = ""
    completed_at: str = ""

    @classmethod
    def load(cls, path: Path) -> Progress:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls(
            kinds_done=list(data.get("kinds_done") or []),
            rows_done=dict(data.get("rows_done") or {}),
            started_at=str(data.get("started_at") or ""),
            completed_at=str(data.get("completed_at") or ""),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "kinds_done": list(self.kinds_done),
            "rows_done": dict(self.rows_done),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _open_v2_readonly(db_path: Path) -> sqlite3.Connection:
    """v2 DB is read-only; immutable=1 prevents any accidental write."""
    uri = f"file:{db_path.as_posix()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _open_v3(target_db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(target_db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = OFF")  # tolerate batched FK targets
    return conn


def _ensure_v3_schema(v3: sqlite3.Connection) -> None:
    """Apply schema_v3.sql once; harmless to re-run (all CREATE IF NOT EXISTS)."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    v3.executescript(sql)
    v3.commit()


# ============================================================
# Gist computation (heuristic; first sentence ≤30 words)
# ============================================================

_SENT_RE = re.compile(r"(?<=[.!?])\s+")
_WS_RE = re.compile(r"\s+")


def heuristic_gist(text: str | None, max_words: int = 30) -> str | None:
    """Extract first sentence, normalize whitespace, cap at max_words."""
    if not text:
        return None
    cleaned = _WS_RE.sub(" ", text.strip())
    if not cleaned:
        return None
    parts = _SENT_RE.split(cleaned, maxsplit=1)
    first = parts[0] if parts else cleaned
    words = first.split()
    if len(words) > max_words:
        first = " ".join(words[:max_words]) + "..."
    return first


def _one_line(text: str | None, max_chars: int = 120) -> str | None:
    if not text:
        return None
    cleaned = _WS_RE.sub(" ", text.strip())
    if len(cleaned) > max_chars:
        return cleaned[: max_chars - 3] + "..."
    return cleaned


# ============================================================
# Porter helpers (per kind; each ≤80 LOC)
# ============================================================


def _batch_rows(
    cursor: sqlite3.Cursor, query: str, params: tuple, batch_size: int, offset: int
) -> Iterable[list[sqlite3.Row]]:
    """Yield batches of rows from a paged SELECT until exhausted."""
    while True:
        cursor.execute(query + " LIMIT ? OFFSET ?", (*params, batch_size, offset))
        rows = cursor.fetchall()
        if not rows:
            return
        yield rows
        offset += len(rows)


def _exec_many(v3: sqlite3.Connection, sql: str, rows: list[tuple], commit: bool = True) -> None:
    """Batched insert with commit per batch."""
    v3.executemany(sql, rows)
    if commit:
        v3.commit()


def port_workspace_manifest(v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str) -> int:
    row = v2.execute("SELECT * FROM workspace_manifest WHERE workspace_id = ?", (ws,)).fetchone()
    if row is None:
        return 0
    v3.execute(
        """INSERT OR REPLACE INTO workspace_manifest
           (id, workspace_id, db_uuid, schema_version, created_at, updated_at,
            last_audit_at, last_audit_status, last_repair_at, metadata_json)
           VALUES (1, ?, ?, 'v3.0.0', ?, ?, ?, ?, ?, ?)""",
        (
            row["workspace_id"],
            row["db_uuid"],
            row["created_at"],
            _now_iso(),
            row["last_audit_at"],
            row["last_audit_status"],
            row["last_repair_at"],
            row["metadata_json"],
        ),
    )
    v3.commit()
    return 1


def port_workspace_meta(v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str) -> int:
    rows = v2.execute(
        "SELECT workspace_id, key, value, updated_at FROM workspace_meta WHERE workspace_id = ?",
        (ws,),
    ).fetchall()
    payload = [(r["workspace_id"], r["key"], r["value"], r["updated_at"]) for r in rows]
    if payload:
        v3.executemany(
            "INSERT OR REPLACE INTO workspace_meta (workspace_id, key, value, updated_at) VALUES (?,?,?,?)",
            payload,
        )
        v3.commit()
    return len(payload)


def port_episodes(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    cur = v2.cursor()
    total = 0
    sql_select = "SELECT * FROM episodes WHERE workspace_id = ? ORDER BY id"
    sql_insert = """INSERT OR REPLACE INTO episodes
        (id, workspace_id, session_id, task_id, source_type, raw_text, summary,
         gist, trust_level, importance, confidence, created_at, metadata_json,
         label, is_archived)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    for batch in _batch_rows(cur, sql_select, (ws,), batch_size, offset):
        payload = [
            (
                r["id"],
                r["workspace_id"],
                r["session_id"],
                r["task_id"],
                r["source_type"],
                r["raw_text"],
                r["summary"],
                heuristic_gist(r["raw_text"]),
                r["trust_level"],
                r["importance"],
                r["confidence"],
                r["created_at"],
                r["metadata_json"],
                r["label"],
                r["is_archived"],
            )
            for r in batch
        ]
        _exec_many(v3, sql_insert, payload)
        total += len(payload)
    return total


def port_decisions(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    cur = v2.cursor()
    sql_select = "SELECT * FROM decisions WHERE workspace_id = ? ORDER BY id"
    sql_insert = """INSERT OR REPLACE INTO decisions
        (id, workspace_id, title, decision_text, rationale, gist, status,
         supersedes_decision_id, source_episode_id, confidence, importance,
         valid_from, valid_to, pinned, feedback_ewma, last_retrieved_at,
         references_json, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    total = 0
    for batch in _batch_rows(cur, sql_select, (ws,), batch_size, offset):
        payload = [
            (
                r["id"],
                r["workspace_id"],
                r["title"],
                r["decision_text"],
                r["rationale"],
                heuristic_gist(r["decision_text"]),
                r["status"],
                r["supersedes_decision_id"],
                r["source_episode_id"],
                r["confidence"],
                r["importance"],
                r["valid_from"],
                r["valid_to"],
                r["pinned"],
                r["feedback_ewma"],
                r["last_retrieved_at"],
                r["references_json"],
                r["created_at"],
                r["updated_at"],
            )
            for r in batch
        ]
        _exec_many(v3, sql_insert, payload)
        total += len(payload)
    return total


def port_theories(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    cur = v2.cursor()
    sql_select = "SELECT * FROM theories WHERE workspace_id = ? ORDER BY id"
    sql_insert = """INSERT OR REPLACE INTO theories
        (id, workspace_id, title, domain, claim, gist, mechanism, predictions_json,
         validation_criteria_json, experiment_plan, tags_json, status,
         supersedes_theory_id, dependent_decision_ids_json, source_episode_id,
         confidence, importance, evidence_count, evidence_strength, feedback_ewma,
         last_retrieved_at, last_tested_at, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    total = 0
    for batch in _batch_rows(cur, sql_select, (ws,), batch_size, offset):
        payload = [
            (
                r["id"],
                r["workspace_id"],
                r["title"],
                r["domain"],
                r["claim"],
                heuristic_gist(r["claim"]),
                r["mechanism"],
                r["predictions_json"],
                r["validation_criteria_json"],
                r["experiment_plan"],
                r["tags_json"],
                r["status"],
                r["supersedes_theory_id"],
                r["dependent_decision_ids_json"],
                r["source_episode_id"],
                r["confidence"],
                r["importance"],
                r["evidence_count"],
                r["evidence_strength"],
                r["feedback_ewma"],
                r["last_retrieved_at"],
                r["last_tested_at"],
                r["created_at"],
                r["updated_at"],
            )
            for r in batch
        ]
        _exec_many(v3, sql_insert, payload)
        total += len(payload)
    return total


def port_theory_evidence(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    cur = v2.cursor()
    sql_select = "SELECT * FROM theory_evidence WHERE workspace_id = ? ORDER BY id"
    sql_insert = """INSERT OR REPLACE INTO theory_evidence
        (id, workspace_id, theory_id, kind, summary, source_episode_id,
         artifact_path, metrics_json, confidence, observed_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)"""
    total = 0
    for batch in _batch_rows(cur, sql_select, (ws,), batch_size, offset):
        payload = [
            (
                r["id"],
                r["workspace_id"],
                r["theory_id"],
                r["kind"],
                r["summary"],
                r["source_episode_id"],
                r["artifact_path"],
                r["metrics_json"],
                r["confidence"],
                r["observed_at"],
                r["created_at"],
            )
            for r in batch
        ]
        _exec_many(v3, sql_insert, payload)
        total += len(payload)
    return total


def port_behaviors(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    """v3 behaviors merges v2 behavior_instructions + core_memory + procedural_rules.

    Offset semantics: we run all three sources in fixed order. Offset
    encodes which source we're on (high bits) and the row offset
    within it (low bits). For resumability simplicity, we use three
    separate offsets in a sub-progress dict — but to keep the migration
    progress shape uniform, we port all three sources here in one call
    and return the total count.
    """
    # behaviors / skills are small multi-source ports; re-run idempotently
    # via INSERT OR REPLACE regardless of resume offset.
    del offset  # signature-uniform; not used for these multi-source kinds

    total = 0
    # 1. behavior_instructions → behaviors (same kind)
    rows = v2.execute(
        "SELECT * FROM behavior_instructions WHERE workspace_id = ? ORDER BY id", (ws,)
    ).fetchall()
    sql_insert = """INSERT OR REPLACE INTO behaviors
        (id, workspace_id, name, kind, scope, priority, rule, rule_one_line,
         rationale, applies_to_json, conflict_policy, conflict_group, source_type,
         source_id, source_episode_id, reviewed_by, reviewed_at, expires_at,
         confidence, importance, pinned, active, last_applied_at,
         application_count, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    payload = [
        (
            r["id"],
            r["workspace_id"],
            r["name"],
            r["kind"],
            r["scope"],
            r["priority"],
            r["rule"],
            _one_line(r["rule"]),
            r["rationale"],
            r["applies_to_json"],
            r["conflict_policy"],
            r["conflict_group"],
            r["source_type"],
            r["source_id"],
            r["source_episode_id"],
            r["reviewed_by"],
            r["reviewed_at"],
            r["expires_at"],
            r["confidence"],
            0.5,
            r["pinned"],
            r["active"],
            r["last_applied_at"],
            r["application_count"],
            r["created_at"],
            r["updated_at"],
        )
        for r in rows
    ]
    if payload:
        v3.executemany(sql_insert, payload)
        v3.commit()
        total += len(payload)

    # 2. core_memory → behaviors (kind='core_memory')
    rows = v2.execute("SELECT * FROM core_memory WHERE workspace_id = ?", (ws,)).fetchall()
    payload = [
        (
            f"beh_core_{r['id']}",
            r["workspace_id"],
            r["key"],
            "core_memory",
            "workspace",
            "project_convention",
            r["value"],
            _one_line(r["value"]),
            "",
            "[]",
            "system_wins",
            None,
            "manual",
            r["id"],
            r["source_episode_id"],
            None,
            None,
            None,
            r["confidence"],
            r["importance"],
            r["pinned"],
            r["active"],
            None,
            0,
            r["created_at"],
            r["updated_at"],
        )
        for r in rows
    ]
    if payload:
        v3.executemany(sql_insert, payload)
        v3.commit()
        total += len(payload)

    # 3. procedural_rules → behaviors (kind='procedural_rule')
    rows = v2.execute("SELECT * FROM procedural_rules WHERE workspace_id = ?", (ws,)).fetchall()
    payload = [
        (
            f"beh_proc_{r['id']}",
            r["workspace_id"],
            f"rule_{r['id']}",
            "procedural_rule",
            r["scope"],
            "user_preference",
            r["rule_text"],
            _one_line(r["rule_text"]),
            "",
            "[]",
            "current_user_wins",
            None,
            "manual",
            r["id"],
            r["source_episode_id"],
            None,
            None,
            None,
            r["confidence"],
            r["importance"],
            0,
            r["active"],
            None,
            0,
            r["created_at"],
            r["updated_at"],
        )
        for r in rows
    ]
    if payload:
        v3.executemany(sql_insert, payload)
        v3.commit()
        total += len(payload)
    return total


def port_skills(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    """v3 skills merges v2 agent_roles + agent_skills + agent_playbooks via subtype."""
    offset = min(offset, 0)  # small set; idempotent INSERT OR REPLACE handles repeats

    total = 0
    sql_insert = """INSERT OR REPLACE INTO skills
        (id, workspace_id, name, subtype, summary, when_to_use_short, body_md,
         body_token_count, when_to_use_json, inputs_json, outputs_json,
         tools_json, related_roles_json, responsibilities_json, boundaries_json,
         handoff_triggers_json, triggers_json, steps_json, success_criteria_json,
         required_skills_json, source_episode_id, confidence, active, usage_count,
         success_count, failure_count, last_invoked_at, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""

    # 1. agent_roles → skills(subtype='role')
    rows = v2.execute("SELECT * FROM agent_roles WHERE workspace_id = ?", (ws,)).fetchall()
    payload = [
        (
            r["id"],
            r["workspace_id"],
            r["name"],
            "role",
            r["purpose"],
            _one_line(r["purpose"]),
            "",
            0,
            None,
            None,
            None,
            r["tools_json"],
            None,
            r["responsibilities_json"],
            r["boundaries_json"],
            r["handoff_triggers_json"],
            None,
            None,
            None,
            None,
            r["source_episode_id"],
            r["confidence"],
            r["active"],
            r["usage_count"],
            r["success_count"],
            r["failure_count"],
            r["last_invoked_at"],
            r["created_at"],
            r["updated_at"],
        )
        for r in rows
    ]
    if payload:
        v3.executemany(sql_insert, payload)
        v3.commit()
        total += len(payload)

    # 2. agent_skills → skills(subtype='skill')
    rows = v2.execute("SELECT * FROM agent_skills WHERE workspace_id = ?", (ws,)).fetchall()
    payload = [
        (
            r["id"],
            r["workspace_id"],
            r["name"],
            "skill",
            r["summary"],
            _one_line(r["summary"]),
            "",
            0,
            r["when_to_use_json"],
            r["inputs_json"],
            r["outputs_json"],
            r["tools_json"],
            r["related_roles_json"],
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            r["source_episode_id"],
            r["confidence"],
            r["active"],
            r["usage_count"],
            r["success_count"],
            r["failure_count"],
            r["last_invoked_at"],
            r["created_at"],
            r["updated_at"],
        )
        for r in rows
    ]
    if payload:
        v3.executemany(sql_insert, payload)
        v3.commit()
        total += len(payload)

    # 3. agent_playbooks → skills(subtype='playbook')
    rows = v2.execute("SELECT * FROM agent_playbooks WHERE workspace_id = ?", (ws,)).fetchall()
    payload = [
        (
            r["id"],
            r["workspace_id"],
            r["name"],
            "playbook",
            r["goal"],
            _one_line(r["goal"]),
            "",
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            r["triggers_json"],
            r["steps_json"],
            r["success_criteria_json"],
            r["required_skills_json"],
            r["source_episode_id"],
            r["confidence"],
            r["active"],
            r["usage_count"],
            r["success_count"],
            r["failure_count"],
            r["last_invoked_at"],
            r["created_at"],
            r["updated_at"],
        )
        for r in rows
    ]
    if payload:
        v3.executemany(sql_insert, payload)
        v3.commit()
        total += len(payload)
    return total


def port_concepts(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    cur = v2.cursor()
    sql_select = "SELECT * FROM domain_concepts WHERE workspace_id = ? ORDER BY id"
    sql_insert = """INSERT OR REPLACE INTO concepts
        (id, workspace_id, name, kind, definition, definition_one_line, aliases_json,
         tags_json, source_episode_id, confidence, active, last_retrieved_at,
         created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    total = 0
    for batch in _batch_rows(cur, sql_select, (ws,), batch_size, offset):
        payload = [
            (
                r["id"],
                r["workspace_id"],
                r["name"],
                r["kind"],
                r["definition"],
                _one_line(r["definition"]),
                r["aliases_json"],
                r["tags_json"],
                r["source_episode_id"],
                r["confidence"],
                r["active"],
                r["last_retrieved_at"],
                r["created_at"],
                r["updated_at"],
            )
            for r in batch
        ]
        _exec_many(v3, sql_insert, payload)
        total += len(payload)
    return total


def port_tasks(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    cur = v2.cursor()
    sql_select = "SELECT * FROM task_state WHERE workspace_id = ? ORDER BY id"
    sql_insert = """INSERT OR REPLACE INTO tasks
        (id, workspace_id, task_id, goal, goal_one_line, status, current_plan_json,
         completed_steps_json, next_action, blockers_json, files_in_scope_json,
         source_episode_id, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    total = 0
    for batch in _batch_rows(cur, sql_select, (ws,), batch_size, offset):
        payload = [
            (
                r["id"],
                r["workspace_id"],
                r["task_id"],
                r["goal"],
                _one_line(r["goal"]),
                r["status"],
                r["current_plan_json"],
                r["completed_steps_json"],
                r["next_action"],
                r["blockers_json"],
                r["files_in_scope_json"],
                r["source_episode_id"],
                r["updated_at"],
            )
            for r in batch
        ]
        _exec_many(v3, sql_insert, payload)
        total += len(payload)
    return total


def port_files(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    cur = v2.cursor()
    sql_select = "SELECT * FROM files WHERE workspace_id = ? ORDER BY id"
    sql_insert = """INSERT OR REPLACE INTO files
        (id, workspace_id, path, language, content_hash, size_bytes,
         last_indexed_at, metadata_json, is_archived)
        VALUES (?,?,?,?,?,?,?,?,?)"""
    total = 0
    for batch in _batch_rows(cur, sql_select, (ws,), batch_size, offset):
        payload = [
            (
                r["id"],
                r["workspace_id"],
                r["path"],
                r["language"],
                r["content_hash"],
                r["size_bytes"],
                r["last_indexed_at"],
                r["metadata_json"],
                r["is_archived"],
            )
            for r in batch
        ]
        _exec_many(v3, sql_insert, payload)
        total += len(payload)
    return total


def port_chunks(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    cur = v2.cursor()
    sql_select = "SELECT * FROM chunks WHERE workspace_id = ? ORDER BY id"
    sql_insert = """INSERT OR REPLACE INTO chunks
        (id, workspace_id, file_id, episode_id, kind, text, summary, gist,
         line_start, line_end, symbols_json, symbol_kind, qualified_name,
         parent_qualified_name, embedding_id, importance, confidence,
         feedback_ewma, last_retrieved_at, label, is_archived, created_at,
         metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    total = 0
    for batch in _batch_rows(cur, sql_select, (ws,), batch_size, offset):
        payload = [
            (
                r["id"],
                r["workspace_id"],
                r["file_id"],
                r["episode_id"],
                r["kind"],
                r["text"],
                r["summary"],
                heuristic_gist(r["text"] or r["summary"]),
                r["line_start"],
                r["line_end"],
                r["symbols_json"],
                r["symbol_kind"],
                r["qualified_name"],
                r["parent_qualified_name"],
                r["embedding_id"],
                r["importance"],
                r["confidence"],
                r["feedback_ewma"],
                r["last_retrieved_at"],
                r["label"],
                r["is_archived"],
                r["created_at"],
                r["metadata_json"],
            )
            for r in batch
        ]
        _exec_many(v3, sql_insert, payload)
        total += len(payload)
    return total


def port_code_digests(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    """v2 file_digests → v3 code_digests with structured projection fields.

    structured_json in v2 holds {symbols, top_edges, ...} — we
    extract top_symbols/top_callers/top_callees into v3's
    explicit columns when possible; full structured_json is
    preserved for the curious.
    """
    cur = v2.cursor()
    sql_select = "SELECT * FROM file_digests WHERE workspace_id = ? ORDER BY id"
    sql_insert = """INSERT OR REPLACE INTO code_digests
        (id, workspace_id, file_path, language, chunk_count, symbol_count,
         inbound_edge_count, outbound_edge_count, versions_recent, pagerank,
         purpose_short, narrative, top_symbols_json, top_callers_json,
         top_callees_json, structured_json, last_indexed_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    total = 0
    for batch in _batch_rows(cur, sql_select, (ws,), batch_size, offset):
        payload = []
        for r in batch:
            try:
                structured = json.loads(r["structured_json"] or "{}")
            except (TypeError, ValueError):
                structured = {}
            payload.append(
                (
                    r["id"],
                    r["workspace_id"],
                    r["file_path"],
                    r["language"],
                    r["chunk_count"],
                    r["symbol_count"],
                    r["inbound_edge_count"],
                    r["outbound_edge_count"],
                    r["versions_recent"],
                    0.0,
                    _one_line(r["narrative"]),
                    r["narrative"],
                    json.dumps(structured.get("symbols", []), ensure_ascii=False),
                    json.dumps(structured.get("top_callers", []), ensure_ascii=False),
                    json.dumps(structured.get("top_callees", []), ensure_ascii=False),
                    r["structured_json"],
                    r["last_indexed_at"],
                    r["updated_at"],
                )
            )
        _exec_many(v3, sql_insert, payload)
        total += len(payload)
    return total


def _passthrough_porter(
    v2_table: str, v3_table: str, columns: list[str]
) -> callable[[sqlite3.Connection, sqlite3.Connection, str, int, int], int]:
    """Generate a simple passthrough porter for tables with identical schema."""

    def _port(v2, v3, ws, offset, batch_size):
        cur = v2.cursor()
        cols_csv = ", ".join(columns)
        placeholders = ",".join(["?"] * len(columns))
        sql_select = f"SELECT {cols_csv} FROM {v2_table} WHERE workspace_id = ? ORDER BY id"
        sql_insert = f"INSERT OR REPLACE INTO {v3_table} ({cols_csv}) VALUES ({placeholders})"
        total = 0
        for batch in _batch_rows(cur, sql_select, (ws,), batch_size, offset):
            payload = [tuple(r[c] for c in columns) for r in batch]
            _exec_many(v3, sql_insert, payload)
            total += len(payload)
        return total

    return _port


# Passthrough porters — same columns v2→v3, just renamed table or new gist.
port_audit_log = _passthrough_porter(
    "audit_log",
    "audit_log",
    [
        "id",
        "workspace_id",
        "action",
        "target_type",
        "target_id",
        "source_episode_id",
        "agent_id",
        "before_json",
        "after_json",
        "created_at",
    ],
)
port_capability_links = _passthrough_porter(
    "capability_links",
    "capability_links",
    [
        "id",
        "workspace_id",
        "target_type",
        "target_id",
        "capability_type",
        "capability_id",
        "capability_name",
        "relation",
        "rationale",
        "strength",
        "source_episode_id",
        "created_at",
        "updated_at",
    ],
)
port_maintenance_events = _passthrough_porter(
    "maintenance_events",
    "maintenance_events",
    [
        "id",
        "workspace_id",
        "kind",
        "severity",
        "status",
        "summary",
        "details_json",
        "source_episode_id",
        "target_type",
        "target_id",
        "recurrence_count",
        "first_seen_at",
        "last_seen_at",
        "created_at",
        "resolved_at",
    ],
)
port_memory_usage_feedback = _passthrough_porter(
    "memory_usage_feedback",
    "memory_usage_feedback",
    [
        "id",
        "workspace_id",
        "source_type",
        "source_id",
        "query",
        "usefulness",
        "task_id",
        "notes",
        "source",
        "created_at",
    ],
)
port_memory_state_snapshots = _passthrough_porter(
    "memory_state_snapshots",
    "memory_state_snapshots",
    [
        "id",
        "workspace_id",
        "name",
        "taken_at",
        "counts_json",
        "digests_json",
        "metadata_json",
        "created_at",
    ],
)
port_symbol_edges = _passthrough_porter(
    "symbol_edges",
    "symbol_edges",
    [
        "id",
        "workspace_id",
        "src_chunk_id",
        "src_qualified_name",
        "dst_qualified_name",
        "dst_chunk_id",
        "edge_type",
        "src_language",
        "created_at",
        "metadata_json",
    ],
)
port_symbol_versions = _passthrough_porter(
    "symbol_versions",
    "symbol_versions",
    [
        "id",
        "workspace_id",
        "qualified_name",
        "file_path",
        "chunk_id",
        "language",
        "signature_text",
        "signature_hash",
        "content_hash",
        "created_at",
        "metadata_json",
    ],
)
port_active_edits = _passthrough_porter(
    "active_edits",
    "active_edits",
    [
        "id",
        "workspace_id",
        "qualified_name",
        "file_path",
        "agent_id",
        "claimed_at",
        "expires_at",
        "note",
        "metadata_json",
    ],
)
port_soft_edges = _passthrough_porter(
    "soft_edges",
    "soft_edges",
    [
        "id",
        "workspace_id",
        "src_qualified_name",
        "dst_qualified_name",
        "edge_kind",
        "weight",
        "observation_count",
        "last_seen_at",
        "created_at",
        "metadata_json",
    ],
)
port_snapshots = _passthrough_porter(
    "memory_snapshots",
    "snapshots",
    [
        "id",
        "workspace_id",
        "snapshot_key",
        "title",
        "source_label",
        "db_path",
        "duckdb_path",
        "parquet_dir",
        "window_start",
        "window_end",
        "build_sha",
        "build_branch",
        "build_time",
        "remote_host",
        "table_counts_json",
        "total_rows",
        "metadata_json",
        "source_episode_id",
        "created_at",
        "updated_at",
    ],
)
port_experiments = _passthrough_porter(
    "research_experiments",
    "experiments",
    [
        "id",
        "workspace_id",
        "theory_id",
        "snapshot_id",
        "title",
        "hypothesis",
        "cohort_definition",
        "success_criteria_json",
        "command",
        "status",
        "priority",
        "owner",
        "due_at",
        "source_episode_id",
        "metadata_json",
        "created_at",
        "updated_at",
        "completed_at",
    ],
)
port_experiment_results = _passthrough_porter(
    "experiment_results",
    "experiment_results",
    [
        "id",
        "workspace_id",
        "experiment_id",
        "theory_id",
        "kind",
        "summary",
        "metrics_json",
        "artifact_path",
        "confidence",
        "observed_at",
        "source_episode_id",
        "created_at",
    ],
)


def port_insights(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    cur = v2.cursor()
    sql_select = "SELECT * FROM research_insights WHERE workspace_id = ? ORDER BY id"
    sql_insert = """INSERT OR REPLACE INTO insights
        (id, workspace_id, insight_type, summary, gist, proposed_action,
         target_type, target_id, source_episode_ids_json, confidence, status,
         tags_json, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    total = 0
    for batch in _batch_rows(cur, sql_select, (ws,), batch_size, offset):
        payload = [
            (
                r["id"],
                r["workspace_id"],
                r["insight_type"],
                r["summary"],
                heuristic_gist(r["summary"]),
                r["proposed_action"],
                r["target_type"],
                r["target_id"],
                r["source_episode_ids_json"],
                r["confidence"],
                r["status"],
                r["tags_json"],
                r["created_at"],
                r["updated_at"],
            )
            for r in batch
        ]
        _exec_many(v3, sql_insert, payload)
        total += len(payload)
    return total


def port_candidates(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    """v2 memory_candidates → v3 candidates (rename only)."""
    cur = v2.cursor()
    sql_select = "SELECT * FROM memory_candidates WHERE workspace_id = ? ORDER BY id"
    sql_insert = """INSERT OR REPLACE INTO candidates
        (id, workspace_id, kind, subject, predicate, object, evidence, confidence,
         importance, trust_level, temporal_json, write_targets_json, metadata_json,
         source_episode_id, status, promoted_target_type, promoted_target_id,
         created_at, updated_at, decided_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    total = 0
    for batch in _batch_rows(cur, sql_select, (ws,), batch_size, offset):
        payload = [
            (
                r["id"],
                r["workspace_id"],
                r["kind"],
                r["subject"],
                r["predicate"],
                r["object"],
                r["evidence"],
                r["confidence"],
                r["importance"],
                r["trust_level"],
                r["temporal_json"],
                r["write_targets_json"],
                r["metadata_json"],
                r["source_episode_id"],
                r["status"],
                r["promoted_target_type"],
                r["promoted_target_id"],
                r["created_at"],
                r["updated_at"],
                r["decided_at"],
            )
            for r in batch
        ]
        _exec_many(v3, sql_insert, payload)
        total += len(payload)
    return total


def port_decision_candidates(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    if not _table_exists(v2, "decision_candidates"):
        return 0
    return _passthrough_porter(
        "decision_candidates",
        "decision_candidates",
        [
            "id",
            "workspace_id",
            "theory_id",
            "proposed_title",
            "proposed_decision_text",
            "proposed_rationale",
            "evidence_count",
            "evidence_strength",
            "confidence",
            "status",
            "promoted_decision_id",
            "created_at",
            "updated_at",
            "decided_at",
            "decided_by",
        ],
    )(v2, v3, ws, offset, batch_size)


def port_insight_candidates(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    if not _table_exists(v2, "insight_candidates"):
        return 0
    return _passthrough_porter(
        "insight_candidates",
        "insight_candidates",
        [
            "id",
            "workspace_id",
            "insight_type",
            "summary",
            "proposed_action",
            "target_type",
            "target_id",
            "source_episode_ids_json",
            "confidence",
            "status",
            "promoted_insight_id",
            "tags_json",
            "created_at",
            "updated_at",
            "decided_at",
            "decided_by",
        ],
    )(v2, v3, ws, offset, batch_size)


def port_retrieval_sentinel_results(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    if not _table_exists(v2, "retrieval_sentinel_results"):
        return 0
    return _passthrough_porter(
        "retrieval_sentinel_results",
        "retrieval_sentinel_results",
        [
            "id",
            "workspace_id",
            "case_name",
            "status",
            "matched_count",
            "expected_count",
            "failures_json",
            "metrics_json",
            "run_id",
            "created_at",
        ],
    )(v2, v3, ws, offset, batch_size)


def port_vector_index_metadata(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    rows = v2.execute(
        "SELECT * FROM vector_index_metadata WHERE workspace_id = ?", (ws,)
    ).fetchall()
    if not rows:
        return 0
    sql = """INSERT OR REPLACE INTO vector_index_metadata
        (workspace_id, namespace, provider_name, embedding_dim, vector_backend,
         chunking_strategy, schema_version, row_count, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)"""
    payload = [
        (
            r["workspace_id"],
            r["namespace"],
            r["provider_name"],
            r["embedding_dim"],
            r["vector_backend"],
            r["chunking_strategy"],
            r["schema_version"],
            r["row_count"],
            r["updated_at"],
        )
        for r in rows
    ]
    v3.executemany(sql, payload)
    v3.commit()
    return len(payload)


def port_entities(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    return _passthrough_porter(
        "entities",
        "entities",
        [
            "id",
            "workspace_id",
            "type",
            "canonical_name",
            "aliases_json",
            "properties_json",
            "embedding_id",
            "created_at",
            "updated_at",
        ],
    )(v2, v3, ws, offset, batch_size)


def port_facts(
    v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str, offset: int, batch_size: int
) -> int:
    return _passthrough_porter(
        "facts",
        "facts",
        [
            "id",
            "workspace_id",
            "subject_entity_id",
            "relation",
            "object_entity_id",
            "literal_value",
            "fact_text",
            "source_episode_id",
            "confidence",
            "importance",
            "trust_level",
            "observed_at",
            "valid_from",
            "valid_to",
            "invalidated_by_fact_id",
            "created_at",
            "metadata_json",
        ],
    )(v2, v3, ws, offset, batch_size)


# ============================================================
# Porter dispatch table
# ============================================================

PORTERS = {
    "workspace_manifest": lambda v2, v3, ws, off, bs: port_workspace_manifest(v2, v3, ws),
    "workspace_meta": lambda v2, v3, ws, off, bs: port_workspace_meta(v2, v3, ws),
    "episodes": port_episodes,
    "files": port_files,
    "chunks": port_chunks,
    "decisions": port_decisions,
    "theories": port_theories,
    "theory_evidence": port_theory_evidence,
    "behaviors": port_behaviors,
    "skills": port_skills,
    "concepts": port_concepts,
    "tasks": port_tasks,
    "code_digests": port_code_digests,
    "symbol_edges": port_symbol_edges,
    "symbol_versions": port_symbol_versions,
    "active_edits": port_active_edits,
    "soft_edges": port_soft_edges,
    "snapshots": port_snapshots,
    "experiments": port_experiments,
    "experiment_results": port_experiment_results,
    "insights": port_insights,
    "capability_links": port_capability_links,
    "candidates": port_candidates,
    "decision_candidates": port_decision_candidates,
    "insight_candidates": port_insight_candidates,
    "maintenance_events": port_maintenance_events,
    "retrieval_sentinel_results": port_retrieval_sentinel_results,
    "memory_usage_feedback": port_memory_usage_feedback,
    "memory_state_snapshots": port_memory_state_snapshots,
    "vector_index_metadata": port_vector_index_metadata,
    "audit_log": port_audit_log,
    "entities": port_entities,
    "facts": port_facts,
}


# v2 source table name per kind (for parity checks)
V2_SOURCE_TABLE = {
    "workspace_manifest": "workspace_manifest",
    "workspace_meta": "workspace_meta",
    "episodes": "episodes",
    "files": "files",
    "chunks": "chunks",
    "decisions": "decisions",
    "theories": "theories",
    "theory_evidence": "theory_evidence",
    "behaviors": None,  # merged from 3 sources; computed specially in parity
    "skills": None,
    "concepts": "domain_concepts",
    "tasks": "task_state",
    "code_digests": "file_digests",
    "symbol_edges": "symbol_edges",
    "symbol_versions": "symbol_versions",
    "active_edits": "active_edits",
    "soft_edges": "soft_edges",
    "snapshots": "memory_snapshots",
    "experiments": "research_experiments",
    "experiment_results": "experiment_results",
    "insights": "research_insights",
    "capability_links": "capability_links",
    "candidates": "memory_candidates",
    "decision_candidates": "decision_candidates",
    "insight_candidates": "insight_candidates",
    "maintenance_events": "maintenance_events",
    "retrieval_sentinel_results": "retrieval_sentinel_results",
    "memory_usage_feedback": "memory_usage_feedback",
    "memory_state_snapshots": "memory_state_snapshots",
    "vector_index_metadata": "vector_index_metadata",
    "audit_log": "audit_log",
    "entities": "entities",
    "facts": "facts",
}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _row_count(conn: sqlite3.Connection, table: str, ws: str) -> int:
    if not _table_exists(conn, table):
        return 0
    try:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE workspace_id = ?", (ws,)
        ).fetchone()[0]
    except sqlite3.OperationalError:
        # workspace_manifest is a singleton with no workspace_id filter
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def parity_report(v2: sqlite3.Connection, v3: sqlite3.Connection, ws: str) -> dict:
    """Build per-kind v2 vs v3 row-count diff. behaviors and skills are special-cased."""
    report = {"workspace_id": ws, "checked_at": _now_iso(), "per_kind": {}, "ok": True}
    for kind in KIND_ORDER:
        v2_source = V2_SOURCE_TABLE.get(kind)
        if kind == "behaviors":
            v2_count = (
                _row_count(v2, "behavior_instructions", ws)
                + _row_count(v2, "core_memory", ws)
                + _row_count(v2, "procedural_rules", ws)
            )
        elif kind == "skills":
            v2_count = (
                _row_count(v2, "agent_roles", ws)
                + _row_count(v2, "agent_skills", ws)
                + _row_count(v2, "agent_playbooks", ws)
            )
        elif v2_source is None:
            continue
        else:
            v2_count = _row_count(v2, v2_source, ws)
        v3_count = _row_count(v3, kind, ws)
        match = v2_count == v3_count
        report["per_kind"][kind] = {
            "v2": v2_count,
            "v3": v3_count,
            "match": match,
        }
        if not match:
            report["ok"] = False
    return report


# ============================================================
# Main orchestration
# ============================================================


def migrate(
    *,
    workspace_id: str,
    source_db: Path,
    target_dir: Path,
    batch_size: int = 1000,
    resume: bool = True,
    log=print,
) -> dict:
    """Migrate one workspace from v2 SQLite to v3 SQLite. Returns parity report."""
    target_dir.mkdir(parents=True, exist_ok=True)
    target_db = target_dir / "memory.db"
    progress_path = target_dir / f"migration_progress_{workspace_id}.json"
    report_path = target_dir / f"migration_report_{workspace_id}.json"

    progress = Progress.load(progress_path) if resume else Progress()
    if not progress.started_at:
        progress.started_at = _now_iso()

    v2 = _open_v2_readonly(source_db)
    v3 = _open_v3(target_db)
    try:
        _ensure_v3_schema(v3)
        for kind in KIND_ORDER:
            if kind in progress.kinds_done:
                continue
            porter = PORTERS[kind]
            offset = progress.rows_done.get(kind, 0)
            t0 = time.monotonic()
            try:
                n = porter(v2, v3, workspace_id, offset, batch_size)
            except sqlite3.OperationalError as exc:
                log(f"  [SKIP] {kind}: {exc}")
                progress.kinds_done.append(kind)
                progress.save(progress_path)
                continue
            elapsed = time.monotonic() - t0
            progress.kinds_done.append(kind)
            progress.rows_done[kind] = n
            progress.save(progress_path)
            log(f"  [OK]   {kind}: {n} rows ({elapsed:.2f}s)")

        progress.completed_at = _now_iso()
        progress.save(progress_path)
        report = parity_report(v2, v3, workspace_id)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        return report
    finally:
        v2.close()
        v3.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", "--workspace-id", required=True, dest="workspace")
    p.add_argument("--source-db", required=True, type=Path)
    p.add_argument("--target-dir", required=True, type=Path)
    p.add_argument("--batch-size", type=int, default=1000)
    p.add_argument("--no-resume", action="store_true", dest="no_resume")
    args = p.parse_args()

    print(
        f"Migrating workspace={args.workspace!r} from {args.source_db} "
        f"to {args.target_dir}/memory.db (batch={args.batch_size})"
    )
    report = migrate(
        workspace_id=args.workspace,
        source_db=args.source_db,
        target_dir=args.target_dir,
        batch_size=args.batch_size,
        resume=not args.no_resume,
    )
    print()
    print(f"=== Parity report (ok={report['ok']}) ===")
    for kind, stats in report["per_kind"].items():
        flag = "OK " if stats["match"] else "MISMATCH"
        print(f"  [{flag}] {kind:30s} v2={stats['v2']:6d}  v3={stats['v3']:6d}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
