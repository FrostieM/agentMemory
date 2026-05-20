"""v3.4 #9 — cross-DB transfer for V2 recall tuning tests.

Pins the third-tier fallback contract:

* Default-OFF: ``MEMORY_RECALL_TRANSFER_CROSS_DB_ENABLED=true`` is
  required to fire — pulling tuning signal from unrelated projects
  is opt-in.
* The registry walk skips the current DB so we never double-count.
* Failure-soft: missing registry / stale entries / locked peer DBs
  return None without raising.
* Aggregation matches the same-DB rollup contract: returns
  ``(n, empty_rate, mean_outcome)`` so ``_apply_rules`` can consume it.
* ``suggest_params`` end-to-end: when own + same-DB tiers are thin,
  the cross-DB tier fires and the suggestion carries the
  ``transfer_cross_db:`` reason prefix.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_memory_lite.retrieval import recall_tuning as rt
from agent_memory_lite.retrieval import recall_tuning_cross_db as cdb

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "0034_recall_history.sql"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MEMORY_RECALL_TUNING_ENABLED",
        "MEMORY_RECALL_TUNING_WINDOW_HOURS",
        "MEMORY_RECALL_TUNING_MIN_SAMPLES",
        "MEMORY_RECALL_TRANSFER_ENABLED",
        "MEMORY_RECALL_TRANSFER_MIN_SAMPLES",
        "MEMORY_RECALL_TRANSFER_CROSS_DB_ENABLED",
        "MEMORY_RECALL_TRANSFER_CROSS_DB_MIN_SAMPLES",
        "MEMORY_RECALL_TRANSFER_CROSS_DB_MAX_PEERS",
        "MEMORY_WORKSPACES_FILE",
    ):
        monkeypatch.delenv(name, raising=False)


def _make_db(path: Path) -> None:
    """Create a recall_history DB at ``path`` using the v3.1 migration."""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.close()


def _seed_history(
    path: Path,
    *,
    workspace_id: str,
    n: int,
    hits: int = 5,
    outcome_x100: int = 50,
) -> None:
    """Append ``n`` recall_history rows. ``outcome_x100=50`` gives
    a mean outcome of 0.5 — well above the raise_floor threshold (0.3)
    so any rule application produces a deterministic suggestion."""
    conn = sqlite3.connect(path)
    for i in range(n):
        conn.execute(
            "INSERT INTO recall_history (workspace_id, topic_norm, depth, "
            "outcome_floor_x100, hits_count, avg_outcome_x100, "
            "avg_activation_x1000, created_at) VALUES (?, ?, 2, 0, ?, ?, 500, "
            "datetime('now','-1 hours'))",
            (workspace_id, f"topic_{i}", hits, outcome_x100),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Two peer DBs registered in a fake workspaces.json.

    ``current.db`` is the "calling" workspace's DB (kept empty so the
    own + same-DB tiers can't satisfy the suggestion). ``peer.db``
    holds enough recall_history to satisfy the cross-DB tier."""
    current = tmp_path / "current.db"
    peer = tmp_path / "peer.db"
    _make_db(current)
    _make_db(peer)
    reg_path = tmp_path / "workspaces.json"
    reg_path.write_text(
        json.dumps(
            {
                "version": 1,
                "workspaces": [
                    {"id": "current", "db_path": str(current)},
                    {"id": "peer", "db_path": str(peer)},
                ],
            }
        )
    )
    monkeypatch.setenv("MEMORY_WORKSPACES_FILE", str(reg_path))
    return {"current": current, "peer": peer, "registry": reg_path}


def test_defaults() -> None:
    assert cdb.is_cross_db_enabled() is False  # opt-in
    assert cdb.cross_db_min_samples() == 32
    assert cdb.max_peer_dbs() == 8


def test_disabled_returns_none(registry: dict[str, Path]) -> None:
    """When the env flag is off, no scan happens — even with a fat peer."""
    _seed_history(registry["peer"], workspace_id="peer", n=100)
    assert (
        cdb.rollup_cross_db_stats(current_db_path=str(registry["current"]), window_hours=72) is None
    )


def test_aggregates_across_peers(
    registry: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two peers seeded with 20 + 20 rows → aggregate n=40."""
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_CROSS_DB_ENABLED", "true")
    _seed_history(registry["peer"], workspace_id="peer", n=40)
    result = cdb.rollup_cross_db_stats(current_db_path=str(registry["current"]), window_hours=72)
    assert result is not None
    n, empty_rate, mean_outcome = result
    assert n == 40
    assert empty_rate == pytest.approx(0.0)  # all rows had hits=5 > 0
    assert mean_outcome == pytest.approx(0.5)


def test_skips_the_current_db(registry: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """The current DB must be excluded so the same-DB tier doesn't
    double-count its rows in the cross-DB rollup."""
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_CROSS_DB_ENABLED", "true")
    _seed_history(registry["current"], workspace_id="current", n=100)
    _seed_history(registry["peer"], workspace_id="peer", n=10)
    result = cdb.rollup_cross_db_stats(current_db_path=str(registry["current"]), window_hours=72)
    assert result is not None
    # Only the peer's 10 rows show — current's 100 are correctly excluded.
    assert result[0] == 10


def test_failure_soft_missing_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No workspaces.json → None, not exception."""
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_CROSS_DB_ENABLED", "true")
    monkeypatch.setenv("MEMORY_WORKSPACES_FILE", str(tmp_path / "missing.json"))
    assert (
        cdb.rollup_cross_db_stats(current_db_path=str(tmp_path / "current.db"), window_hours=72)
        is None
    )


def test_failure_soft_stale_peer_path(
    registry: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A registry entry pointing at a deleted DB file is dropped — and
    a valid sibling still produces a result."""
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_CROSS_DB_ENABLED", "true")
    _seed_history(registry["peer"], workspace_id="peer", n=20)
    # Append a third entry pointing at a non-existent file.
    reg_path = registry["registry"]
    data = json.loads(reg_path.read_text())
    data["workspaces"].append({"id": "ghost", "db_path": str(reg_path.parent / "ghost.db")})
    reg_path.write_text(json.dumps(data))
    result = cdb.rollup_cross_db_stats(current_db_path=str(registry["current"]), window_hours=72)
    assert result is not None
    assert result[0] == 20  # the ghost is silently dropped


def test_max_peer_dbs_caps_fanout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With 5 peers and the cap set to 2, only 2 are scanned."""
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_CROSS_DB_ENABLED", "true")
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_CROSS_DB_MAX_PEERS", "2")
    current = tmp_path / "current.db"
    _make_db(current)
    workspaces = [{"id": "current", "db_path": str(current)}]
    for i in range(5):
        p = tmp_path / f"peer_{i}.db"
        _make_db(p)
        _seed_history(p, workspace_id=f"peer_{i}", n=10)
        workspaces.append({"id": f"peer_{i}", "db_path": str(p)})
    reg_path = tmp_path / "workspaces.json"
    reg_path.write_text(json.dumps({"version": 1, "workspaces": workspaces}))
    monkeypatch.setenv("MEMORY_WORKSPACES_FILE", str(reg_path))
    result = cdb.rollup_cross_db_stats(current_db_path=str(current), window_hours=72)
    assert result is not None
    # Cap=2 → at most 20 rows total, not 50.
    assert result[0] == 20


def test_suggest_params_fires_cross_db_tier(
    registry: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: own + same-DB tiers are thin, cross-DB tier has
    32+ rows with high outcome → suggestion fires with the
    ``transfer_cross_db:`` reason prefix."""
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_CROSS_DB_ENABLED", "true")
    # Same-DB transfer must be OFF/empty so we don't satisfy tier 2 first.
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_ENABLED", "false")
    _seed_history(registry["peer"], workspace_id="peer", n=40, outcome_x100=50)
    conn = sqlite3.connect(registry["current"])
    try:
        result = rt.suggest_params(conn, workspace_id="current", base_depth=2, base_floor=0.0)
    finally:
        conn.close()
    assert result is not None
    assert result.reason.startswith("transfer_cross_db:"), result.reason
    # Mean outcome 0.5 ≥ 0.3 threshold → raise_floor rule fires.
    assert result.reason.endswith("raise_floor")


def test_suggest_params_skips_cross_db_when_same_db_satisfies(
    registry: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tier 2 should beat tier 3 — if same-DB has enough samples, the
    suggestion carries the ``transfer:`` (not ``transfer_cross_db:``)
    prefix and the cross-DB scan never runs."""
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_ENABLED", "true")
    monkeypatch.setenv("MEMORY_RECALL_TRANSFER_CROSS_DB_ENABLED", "true")
    # Seed enough rows in the CURRENT DB but a different workspace_id
    # so tier 1 (own-workspace) is thin and tier 2 (same-DB transfer)
    # is fat.
    _seed_history(registry["current"], workspace_id="sibling", n=20)
    conn = sqlite3.connect(registry["current"])
    try:
        result = rt.suggest_params(conn, workspace_id="current", base_depth=2, base_floor=0.0)
    finally:
        conn.close()
    assert result is not None
    assert result.reason.startswith("transfer:")
    assert "cross_db" not in result.reason
