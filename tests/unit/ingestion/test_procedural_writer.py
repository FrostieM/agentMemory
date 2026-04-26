from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.procedural_writer import (
    archive_procedural_rule,
    write_procedural_rule,
)
from agent_memory_lite.models.procedural import ProceduralRuleIn
from agent_memory_lite.repositories.procedural_repo import list_active_rules


def _rule(**overrides: object) -> ProceduralRuleIn:
    payload: dict[str, object] = {
        "workspace_id": "default",
        "rule_text": "Before editing files, retrieve memory for those files.",
    }
    payload.update(overrides)
    return ProceduralRuleIn(**payload)


def test_write_creates_active_rule(applied_conn: sqlite3.Connection) -> None:
    rule = write_procedural_rule(applied_conn, _rule())
    assert rule.active is True
    assert rule.id.startswith("rule_")


def test_archive_deactivates(applied_conn: sqlite3.Connection) -> None:
    rule = write_procedural_rule(applied_conn, _rule())
    rows = archive_procedural_rule(applied_conn, workspace_id="default", rule_id=rule.id)
    assert rows == 1
    actives = list_active_rules(applied_conn, "default")
    assert all(r.id != rule.id for r in actives)


def test_workspace_listing(applied_conn: sqlite3.Connection) -> None:
    write_procedural_rule(applied_conn, _rule(workspace_id="default", rule_text="r1"))
    write_procedural_rule(applied_conn, _rule(workspace_id="other", rule_text="r2"))
    default_rules = {r.rule_text for r in list_active_rules(applied_conn, "default")}
    other_rules = {r.rule_text for r in list_active_rules(applied_conn, "other")}
    assert default_rules == {"r1"}
    assert other_rules == {"r2"}
