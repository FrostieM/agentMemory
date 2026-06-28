"""H1 (reliability audit 2026-06-26): the else-branch durable kinds (insight /
skill / concept / snapshot / code_digest) and ``issue`` route straight through
storage.writer.write with NO secret redaction, so a pasted secret landed
cleartext on disk AND in the durable_fts BM25 index -- violating the project's
hard 'Never store secrets' rule. redact_freetext_fields at the create choke
point (write_canonical) + the edit choke point closes every path.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.ingestion.canonical_writer import write_canonical
from agent_memory_lite.storage.writer import edit

# A secret the redactor reliably catches (same shape the episode redaction test
# uses). The raw value must never survive in any persisted column or FTS index.
_RAW = "sk-ant-secret-LEAK-AAAA"
_SECRET = f"api_key: {_RAW}"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def _col(conn: sqlite3.Connection, table: str, col: str, id_: str) -> str:
    row = conn.execute(f"SELECT {col} FROM {table} WHERE id = ?", (id_,)).fetchone()
    return str(row[0] or "") if row is not None else ""


def _fts_content(conn: sqlite3.Connection, object_id: str) -> str:
    row = conn.execute(
        "SELECT content FROM durable_fts WHERE object_id = ?", (object_id,)
    ).fetchone()
    return str(row[0] or "") if row is not None else ""


def test_insight_secret_redacted_on_disk_and_in_durable_fts(conn: sqlite3.Connection) -> None:
    out = write_canonical(
        conn,
        workspace_id="ws",
        kind="insight",
        payload={"summary": _SECRET, "insight_type": "lesson", "status": "new"},
    )
    assert out is not None
    summary = _col(conn, "insights", "summary", str(out["id"]))
    assert _RAW not in summary
    assert "<<REDACTED" in summary
    # insight is a DURABLE_FTS_KIND -> the secret must not survive in the index.
    assert _RAW not in _fts_content(conn, str(out["id"]))


def test_skill_and_concept_secrets_redacted(conn: sqlite3.Connection) -> None:
    sk = write_canonical(
        conn,
        workspace_id="ws",
        kind="skill",
        payload={"name": "deploy", "summary": _SECRET, "when_to_use_short": "x"},
    )
    assert sk is not None
    assert _RAW not in _col(conn, "skills", "summary", str(sk["id"]))
    assert _RAW not in _fts_content(conn, str(sk["id"]))

    co = write_canonical(
        conn,
        workspace_id="ws",
        kind="concept",
        payload={"name": "thing", "definition": _SECRET},
    )
    assert co is not None
    assert _RAW not in _col(conn, "concepts", "definition", str(co["id"]))
    assert _RAW not in _fts_content(conn, str(co["id"]))


def test_issue_secret_redacted_in_title_and_derived_signature(conn: sqlite3.Connection) -> None:
    out = write_canonical(
        conn,
        workspace_id="ws",
        kind="issue",
        payload={"title": f"crash near {_SECRET}", "category": "bug"},
    )
    assert out is not None
    assert _RAW not in _col(conn, "issues", "title", str(out["id"]))
    # The dedup signature is derived from the title -- redaction must happen
    # BEFORE it is computed, so the secret never lands in issues.signature either.
    assert _RAW not in _col(conn, "issues", "signature", str(out["id"]))


def test_edit_redacts_reintroduced_secret(conn: sqlite3.Connection) -> None:
    out = write_canonical(
        conn,
        workspace_id="ws",
        kind="concept",
        payload={"name": "clean", "definition": "nothing secret here"},
    )
    assert out is not None
    edit(
        conn,
        workspace_id="ws",
        kind="concept",
        object_id=str(out["id"]),
        fields={"definition": _SECRET},
    )
    assert _RAW not in _col(conn, "concepts", "definition", str(out["id"]))
    assert _RAW not in _fts_content(conn, str(out["id"]))


def test_redact_freetext_fields_handles_lists_dicts_and_json_strings() -> None:
    """H1-F1 (audit round 1): a secret can hide in a LIST element, a nested dict
    value, or a pre-serialized *_json string -- not just a top-level string. The
    helper must redact all three while leaving control keys + structure intact."""
    from agent_memory_lite.redaction.payload import redact_freetext_fields  # noqa: PLC0415

    out = redact_freetext_fields(
        {
            "summary": _SECRET,  # top-level string
            "aliases": [_SECRET, "ordinary"],  # list element
            "meta": {"note": _SECRET, "ok": "fine"},  # nested dict value
            "tags_json": json.dumps([_SECRET, "keep"]),  # pre-serialized json string
            "id": f"ins_{_RAW}",  # control key: must pass through UNTOUCHED
            "status": "new",  # control key
        }
    )
    assert _RAW not in out["summary"]
    assert _RAW not in out["aliases"][0]
    assert out["aliases"][1] == "ordinary"  # non-secret element preserved
    assert _RAW not in out["meta"]["note"]
    assert out["meta"]["ok"] == "fine"
    assert _RAW not in out["tags_json"]
    assert "keep" in out["tags_json"]  # valid JSON + non-secret element preserved
    assert out["id"] == f"ins_{_RAW}"  # control key never mangled (even if secret-shaped)
    assert out["status"] == "new"


def test_redact_freetext_fields_bounds_deep_nesting_and_bad_json_no_crash() -> None:
    """H1 (audit rounds 2-3): pathological input must NOT crash the write.
    ``_redact_value`` is depth-bounded (no RecursionError on a deep DIRECT nest,
    which never touches json.loads), and ``_redact_json_string`` returns an
    unparseable *_json string unchanged. Kept deterministic -- no giant
    json.loads whose recursion depends on the ambient call stack."""
    from agent_memory_lite.redaction.payload import redact_freetext_fields  # noqa: PLC0415

    deep: object = "x"
    for _ in range(2000):  # far past Python's recursion limit; the depth bound stops at 25
        deep = [deep]
    out = redact_freetext_fields(
        {
            "definition": deep,  # deep direct nest -> depth-bounded, no RecursionError
            "aliases_json": "{not: valid, json",  # unparseable -> returned unchanged
            "tags_json": json.dumps(["[" + _SECRET + "]", "ok"]),  # valid -> redacted
        }
    )
    assert "definition" in out  # returned, no crash
    assert out["aliases_json"] == "{not: valid, json"  # invalid json passes through untouched
    assert _RAW not in out["tags_json"]  # valid json still gets its secret redacted
    assert "ok" in out["tags_json"]


def test_concept_aliases_json_secret_redacted_on_disk(conn: sqlite3.Connection) -> None:
    """H1-F1 end-to-end: a secret in a pre-serialized aliases_json string (the
    else-branch leak vector that bypassed the old blanket _json skip) is redacted
    on disk and the column stays valid JSON."""
    co = write_canonical(
        conn,
        workspace_id="ws",
        kind="concept",
        payload={
            "name": "x",
            "definition": "y",
            "aliases_json": json.dumps([_SECRET, "ordinary"]),
        },
    )
    assert co is not None
    aliases_json = _col(conn, "concepts", "aliases_json", str(co["id"]))
    assert _RAW not in aliases_json
    assert "ordinary" in aliases_json
    assert json.loads(aliases_json) is not None  # still valid JSON


def test_issue_category_severity_secret_redacted(conn: sqlite3.Connection) -> None:
    """H1 (audit round 3): issue.category/severity are NOT enum-enforced at the
    v3 write boundary and are returned VERBATIM in the issue projection, so a
    secret pasted there must be redacted too (they used to be skip-listed)."""
    out = write_canonical(
        conn,
        workspace_id="ws",
        kind="issue",
        payload={
            "title": "crash",
            "category": f"cat {_SECRET}",
            "severity": f"sev {_SECRET}",
        },
    )
    assert out is not None
    assert _RAW not in _col(conn, "issues", "category", str(out["id"]))
    assert _RAW not in _col(conn, "issues", "severity", str(out["id"]))


def test_decision_self_redaction_not_regressed_control(conn: sqlite3.Connection) -> None:
    # decision redacts in its own business writer; the new choke-point pass is
    # idempotent and must not regress that (or double-mangle the text).
    out = write_canonical(
        conn,
        workspace_id="ws",
        kind="decision",
        payload={"title": "plan", "decision_text": _SECRET},
    )
    assert out is not None
    text = _col(conn, "decisions", "decision_text", str(out["id"]))
    assert _RAW not in text
    assert "<<REDACTED" in text
