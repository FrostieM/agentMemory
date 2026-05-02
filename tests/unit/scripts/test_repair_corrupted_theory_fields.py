"""Unit tests for the corrupted-theory-fields repair script.

The script targets a real symptom we hit on copyBot: an agent wrote a
theory through ``memory_write_theory`` and the tool-form XML escape
(``<parameter name="...">VAL</parameter>``) leaked into the ``claim``
column. The DB stored exactly what the agent sent, so ``mechanism`` and
``predictions_json`` ended up empty even though the values were buried
in ``claim``. These tests pin the parser and the apply path so the
repair stays correct as we add new edge cases.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import repair_corrupted_theory_fields as repair  # noqa: E402


def _make_db(tmp_path: Path) -> Path:
    """Build a minimal theories table with the columns the script touches."""
    db = tmp_path / "memory.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE theories (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            title TEXT,
            domain TEXT,
            claim TEXT,
            mechanism TEXT,
            predictions_json TEXT,
            experiment_plan TEXT,
            tags_json TEXT,
            validation_criteria_json TEXT,
            dependent_decision_ids_json TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    return db


def _insert(db: Path, **fields: str) -> None:
    conn = sqlite3.connect(str(db))
    cols = ",".join(fields)
    placeholders = ",".join("?" for _ in fields)
    conn.execute(
        f"INSERT INTO theories ({cols}) VALUES ({placeholders})",
        tuple(fields.values()),
    )
    conn.commit()
    conn.close()


def test_extract_param_blocks_handles_unclosed_tail() -> None:
    """The last <parameter> tag in the leaked text often has no closing tag."""
    text = (
        "real claim text.</claim>\n"
        '<parameter name="mechanism">closed mechanism text.</parameter>\n'
        '<parameter name="predictions">["a", "b"]'
    )
    blocks, first_open = repair._extract_param_blocks(text)
    assert first_open == text.index("<parameter")
    names = [name for name, _ in blocks]
    assert names == ["mechanism", "predictions"]
    assert blocks[1][1] == '["a", "b"]'


def test_plan_repair_moves_fields_and_cleans_claim(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    _insert(
        db,
        id="th_test",
        workspace_id="ws_test",
        title="Test theory",
        claim=(
            "Real claim sentence.</claim>\n"
            '<parameter name="mechanism">M body.</parameter>\n'
            '<parameter name="predictions">["p1", "p2"]'
        ),
        mechanism="",
        predictions_json="[]",
        experiment_plan="",
        tags_json="[]",
    )
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM theories").fetchall()
    plan = repair._plan_repair(rows[0])
    assert plan is not None
    assert plan.extracted == {
        "mechanism": "M body.",
        "predictions_json": ["p1", "p2"],
    }
    assert plan.cleaned_fields["claim"] == "Real claim sentence."
    conn.close()


def test_apply_repair_writes_extracted_and_cleaned(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    _insert(
        db,
        id="th_test",
        workspace_id="ws_test",
        title="Test theory",
        claim=('Original claim.</claim>\n<parameter name="mechanism">Mechanism text.</parameter>'),
        mechanism="",
        predictions_json="[]",
        experiment_plan='Real plan.</parameter>\n<parameter name="tags">["x"]',
        tags_json="[]",
    )
    rc = repair.main(["--workspace", "ws_test", "--db-path", str(db), "--apply", "--json"])
    assert rc == 0

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM theories WHERE id='th_test'").fetchone()
    assert row["claim"] == "Original claim."
    assert row["mechanism"] == "Mechanism text."
    assert row["experiment_plan"] == "Real plan."
    assert json.loads(row["tags_json"]) == ["x"]
    conn.close()


def test_skip_when_target_column_already_set(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    _insert(
        db,
        id="th_test",
        workspace_id="ws_test",
        title="Test theory",
        claim=('Real claim.</claim>\n<parameter name="mechanism">would-be-extracted.</parameter>'),
        mechanism="already set, do not overwrite",
        predictions_json="[]",
    )
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM theories").fetchall()
    plan = repair._plan_repair(rows[0])
    assert plan is not None
    # Mechanism column is already populated -> the block is skipped, but
    # the script still cleans the leaked tags out of claim.
    assert plan.extracted == {}
    assert plan.cleaned_fields["claim"] == "Real claim."
    assert any("mechanism:column-already-set" in s for s in plan.skipped)
    conn.close()


def test_dry_run_does_not_mutate(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    original_claim = 'Claim text.</claim>\n<parameter name="mechanism">M.</parameter>'
    _insert(
        db,
        id="th_test",
        workspace_id="ws_test",
        title="Test",
        claim=original_claim,
        mechanism="",
        predictions_json="[]",
    )
    rc = repair.main(["--workspace", "ws_test", "--db-path", str(db), "--json"])
    assert rc == 0
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT claim, mechanism FROM theories").fetchone()
    assert row[0] == original_claim
    assert row[1] == ""
    conn.close()


def test_returns_nonzero_for_missing_db(tmp_path: Path) -> None:
    rc = repair.main(["--workspace", "ws", "--db-path", str(tmp_path / "nope.db"), "--json"])
    assert rc == 2


@pytest.mark.parametrize(
    ("name", "raw", "expected"),
    [
        ("mechanism", " hello ", "hello"),
        ("predictions", '["a","b"]', ["a", "b"]),
        ("predictions", "not json", ["not json"]),
        ("predictions", "", []),
    ],
)
def test_coerce_handles_list_and_string_columns(name: str, raw: str, expected: object) -> None:
    column = repair._EMBEDDED_FIELDS[name]
    assert column is not None
    assert repair._coerce(column, raw) == expected
