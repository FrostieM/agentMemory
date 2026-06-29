"""M2 (write-atomicity batch): write / edit / rollback are each ONE atomic unit.

Connections are autocommit (``isolation_level=None``), so before M2 the
snapshot, the row mutation, the FTS re-sync, and the audit append each
committed *separately*. A failure between them left observable partial state:
an orphan ``versions`` snapshot with no matching mutation, or a mutated row
with no audit row (violating ``storage.writer``'s every-mutation-appends-audit
invariant). M2 wraps each path in ``with_tx`` (a real BEGIN here, a SAVEPOINT
under an outer tx), so a fault anywhere in the unit rolls the WHOLE unit back.

These tests inject the fault at the LAST write (``_audit``) -- the worst case,
because every earlier write in the unit has already executed and must be
undone. ``applied_conn`` is the production autocommit connection, so a passing
assertion here means the atomicity comes from ``with_tx``, not from an ambient
test transaction.
"""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.ingestion.canonical_writer import write_canonical
from agent_memory_lite.storage.writer import edit, rollback, write

# _audit is monkeypatched by string path below; the writer module is the patch
# target, so no module alias is needed here.
_AUDIT_ATTR = "agent_memory_lite.storage.writer._audit"


def _version_count(conn: sqlite3.Connection, target_id: str) -> int:
    row = conn.execute("SELECT COUNT(*) FROM versions WHERE target_id = ?", (target_id,)).fetchone()
    return int(row[0])


def _make_concept(conn: sqlite3.Connection, definition: str) -> str:
    """Create a concept via the committed create choke point. Returns its id.

    concept routes through write_canonical's else-branch -> storage.writer.write,
    so a later direct ``W.write`` with the same id exercises the UPDATE path.
    """
    out = write_canonical(
        conn,
        workspace_id="ws",
        kind="concept",
        payload={"name": "atomicity probe", "definition": definition},
    )
    assert out is not None
    return str(out["id"])


def _raise_audit(*_a: object, **_k: object) -> None:
    raise sqlite3.OperationalError("forced audit-append failure")


def test_write_update_rolls_back_mutation_and_snapshot_on_audit_failure(
    applied_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """write() UPDATE path: a failed ``_audit`` rolls back BOTH the row mutation
    and the pre-update version snapshot -- no orphan version, no half-applied row."""
    cid = _make_concept(applied_conn, "original definition")
    assert _version_count(applied_conn, cid) == 0  # first create takes no snapshot

    monkeypatch.setattr(_AUDIT_ATTR, _raise_audit)
    with pytest.raises(sqlite3.OperationalError, match="forced audit-append failure"):
        write(
            applied_conn,
            workspace_id="ws",
            kind="concept",
            payload={"id": cid, "name": "atomicity probe", "definition": "MUTATED"},
        )

    row = applied_conn.execute("SELECT definition FROM concepts WHERE id = ?", (cid,)).fetchone()
    assert row["definition"] == "original definition"  # mutation rolled back
    assert _version_count(applied_conn, cid) == 0  # snapshot rolled back (no orphan)


def test_edit_rolls_back_mutation_and_snapshot_on_audit_failure(
    applied_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """edit() path: a failed ``_audit`` rolls back the UPDATE and the snapshot."""
    cid = _make_concept(applied_conn, "original definition")
    assert _version_count(applied_conn, cid) == 0

    monkeypatch.setattr(_AUDIT_ATTR, _raise_audit)
    with pytest.raises(sqlite3.OperationalError, match="forced audit-append failure"):
        edit(
            applied_conn,
            workspace_id="ws",
            kind="concept",
            object_id=cid,
            fields={"definition": "MUTATED"},
        )

    row = applied_conn.execute("SELECT definition FROM concepts WHERE id = ?", (cid,)).fetchone()
    assert row["definition"] == "original definition"  # UPDATE rolled back
    assert _version_count(applied_conn, cid) == 0  # snapshot rolled back


def test_rollback_rolls_back_restore_and_snapshot_on_audit_failure(
    applied_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rollback() path: a failed ``_audit`` leaves the live row at its current
    (post-edit) content and writes no pre-rollback snapshot -- the restore is
    undone as a unit."""
    out = write_canonical(
        applied_conn,
        workspace_id="ws",
        kind="decision",
        payload={"title": "atomic decision", "decision_text": "v1 original"},
    )
    assert out is not None
    did = str(out["id"])
    edit(
        applied_conn,
        workspace_id="ws",
        kind="decision",
        object_id=did,
        fields={"decision_text": "v2 edited"},
    )
    versions_before = _version_count(applied_conn, did)  # the pre-edit snapshot

    monkeypatch.setattr(_AUDIT_ATTR, _raise_audit)
    with pytest.raises(sqlite3.OperationalError, match="forced audit-append failure"):
        rollback(
            applied_conn,
            workspace_id="ws",
            kind="decision",
            object_id=did,
            to_version=1,
            why="atomicity probe",
        )

    row = applied_conn.execute(
        "SELECT decision_text FROM decisions WHERE id = ?", (did,)
    ).fetchone()
    assert row["decision_text"] == "v2 edited"  # restore rolled back
    assert _version_count(applied_conn, did) == versions_before  # no pre-rollback snapshot leaked
