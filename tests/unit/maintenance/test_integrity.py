from __future__ import annotations

import json
import sqlite3

import numpy as np

from agent_memory_lite.ingestion.capability_link_writer import link_capability
from agent_memory_lite.ingestion.capability_writer import upsert_agent_skill
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.ingestion.theory_writer import write_theory
from agent_memory_lite.maintenance.integrity import repair_fts, run_integrity_audit
from agent_memory_lite.models.capabilities import AgentSkillIn
from agent_memory_lite.models.capability_links import CapabilityLinkIn
from agent_memory_lite.models.enums import (
    CapabilityLinkRelation,
    CapabilityLinkTargetType,
    CapabilityType,
    EpisodeSource,
    TrustLevel,
)
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.theories import TheoryIn
from agent_memory_lite.repositories.chunks_repo import EMBEDDING_TEXT_SHA256_KEY
from agent_memory_lite.repositories.workspace_manifest_repo import ensure_workspace_manifest
from agent_memory_lite.vector_store.base import VectorRow


def _episode(text: str, *, workspace_id: str = "project-a") -> EpisodeIn:
    return EpisodeIn(
        workspace_id=workspace_id,
        source_type=EpisodeSource.AGENT_ACTION,
        raw_text=text,
        trust_level=TrustLevel.AGENT_OBSERVED,
    )


def test_integrity_detects_fts_workspace_mismatch(applied_conn: sqlite3.Connection) -> None:
    result = ingest_episode(applied_conn, _episode("retrieval parity control token"))
    applied_conn.execute(
        "UPDATE chunks_fts SET workspace_id = 'default' WHERE chunk_id = ?",
        (result.chunk.id,),
    )

    report = run_integrity_audit(applied_conn, workspace_id="project-a")

    assert report.status == "degraded"
    assert report.checks["fts"].details["workspace_mismatch"] == 1
    assert "fts" in report.failures


def test_repair_fts_restores_parity(applied_conn: sqlite3.Connection) -> None:
    result = ingest_episode(applied_conn, _episode("repair parity control token"))
    applied_conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (result.chunk.id,))

    before = run_integrity_audit(applied_conn, workspace_id="project-a")
    assert before.checks["fts"].status == "degraded"

    inserted = repair_fts(applied_conn, workspace_id="project-a")
    after = run_integrity_audit(applied_conn, workspace_id="project-a")

    assert inserted == 1
    assert after.checks["fts"].status == "ok"


def test_integrity_detects_vector_missing(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    ingest_episode(
        applied_conn,
        _episode("vector parity control token"),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )
    fake_vector_store.drop_namespace("chunks")

    report = run_integrity_audit(
        applied_conn,
        workspace_id="project-a",
        vector_store=fake_vector_store,
    )

    assert report.status == "degraded"
    assert report.checks["vector"].details["missing"] == 1
    assert "vector" in report.failures


def test_integrity_recommends_force_rebuild_for_extra_vectors(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    ingest_episode(
        applied_conn,
        _episode("vector extra control token"),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )
    fake_vector_store.upsert(
        "chunks",
        [
            VectorRow(
                id="chk_extra",
                workspace_id="project-a",
                vector=np.zeros(fake_embedding_provider.dim, dtype=np.float32),
            )
        ],
    )

    report = run_integrity_audit(
        applied_conn,
        workspace_id="project-a",
        vector_store=fake_vector_store,
    )

    assert report.checks["vector"].details["extra"] == 1
    assert any("--rebuild-vectors-force --backup-first" in hint for hint in report.repair_hints)


def test_integrity_warns_when_vector_reference_missing(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    result = ingest_episode(
        applied_conn,
        _episode("vector reference control token"),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )
    assert result.chunk.embedding_id == result.chunk.id

    applied_conn.execute(
        "UPDATE chunks SET embedding_id = NULL WHERE id = ?",
        (result.chunk.id,),
    )

    report = run_integrity_audit(
        applied_conn,
        workspace_id="project-a",
        vector_store=fake_vector_store,
    )

    assert report.status == "warning"
    assert report.checks["vector"].status == "warning"
    assert report.checks["vector"].details["missing_embedding_ids"] == 1


def test_integrity_warns_when_embedding_text_hash_missing(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    result = ingest_episode(
        applied_conn,
        _episode("vector hash missing control token"),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )
    applied_conn.execute(
        "UPDATE chunks SET metadata_json = '{}' WHERE id = ?",
        (result.chunk.id,),
    )

    report = run_integrity_audit(
        applied_conn,
        workspace_id="project-a",
        vector_store=fake_vector_store,
    )

    assert report.status == "warning"
    assert report.checks["vector"].status == "warning"
    assert report.checks["vector"].details["missing_embedding_hashes"] == 1
    assert report.checks["vector"].details["stale_embedding_hashes"] == 0


def test_integrity_detects_stale_embedding_text_hash(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    result = ingest_episode(
        applied_conn,
        _episode("vector hash stale control token"),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )
    applied_conn.execute(
        "UPDATE chunks SET text = ? WHERE id = ?",
        ("vector hash changed after embedding", result.chunk.id),
    )

    report = run_integrity_audit(
        applied_conn,
        workspace_id="project-a",
        vector_store=fake_vector_store,
    )

    assert report.status == "degraded"
    assert report.checks["vector"].status == "degraded"
    assert report.checks["vector"].details["stale_embedding_hashes"] == 1
    assert result.chunk.id in report.checks["vector"].details["stale_embedding_hash_ids_sample"]


def test_integrity_detects_stale_vector_metadata(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    ingest_episode(
        applied_conn,
        _episode("vector metadata stale model token"),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )

    report = run_integrity_audit(
        applied_conn,
        workspace_id="project-a",
        vector_store=fake_vector_store,
        expected_provider_name="fake:other-model",
        expected_vector_backend=fake_vector_store.backend,
    )

    assert report.status == "degraded"
    assert report.checks["vector"].details["metadata_status"] == "degraded"
    assert (
        report.checks["vector"].details["metadata"]["mismatches"]["provider_name"]["actual"]
        == "fake:test-64"
    )


def test_ingest_episode_records_embedding_text_hash(
    applied_conn: sqlite3.Connection,
    fake_embedding_provider,
    fake_vector_store,
) -> None:
    result = ingest_episode(
        applied_conn,
        _episode("vector hash persisted control token"),
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
    )

    row = applied_conn.execute(
        "SELECT metadata_json FROM chunks WHERE id = ?",
        (result.chunk.id,),
    ).fetchone()
    metadata = json.loads(row["metadata_json"])

    assert isinstance(metadata.get(EMBEDDING_TEXT_SHA256_KEY), str)
    assert len(metadata[EMBEDDING_TEXT_SHA256_KEY]) == 64


def test_integrity_detects_default_workspace_pollution(
    applied_conn: sqlite3.Connection,
) -> None:
    ingest_episode(applied_conn, _episode("default pollution", workspace_id="default"))

    report = run_integrity_audit(applied_conn, workspace_id="project-a")

    assert report.status == "degraded"
    assert report.checks["workspace_pollution"].details["default_rows"]


def test_integrity_unregistered_foreign_workspace_is_pollution(
    applied_conn: sqlite3.Connection,
) -> None:
    """A foreign workspace id that the registry does NOT mention is
    still flagged as pollution — the check shouldn't go silent just
    because we taught it about registered hub-mode mates."""
    ingest_episode(applied_conn, _episode("rogue write", workspace_id="rogue-ws"))

    report = run_integrity_audit(applied_conn, workspace_id="project-a")

    assert report.status == "degraded"
    poll = report.checks["workspace_pollution"]
    assert "rogue-ws" not in {"project-a", "default"}
    assert poll.details["unregistered_foreign_rows"].get("episodes") == 1
    assert poll.details["registered_foreign_rows"] == {}


def test_integrity_registered_foreign_workspace_is_not_pollution(
    applied_conn: sqlite3.Connection,
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """When the foreign workspace id is REGISTERED against the same
    DB file in workspaces.json, hub-mode is doing what it's designed
    to do. The check must not raise degraded on that — that was the
    v3.4 false positive that buried the real ``vector`` failure
    under audit noise on agent-memory-lite itself."""
    # Build a registry that lists both project-a (target) and a
    # cohabiting hub mate against the same DB file.
    db_path = tmp_path / "memory.db"
    # Touch the file so resolve() works on Windows.
    db_path.write_bytes(b"")
    registry_payload = {
        "workspaces": [
            {
                "id": "project-a",
                "db_path": str(db_path),
                "vector_path": str(tmp_path / "vectors.lance"),
                "label": "host",
                "project_root": str(tmp_path),
            },
            {
                "id": "hub-mate",
                "db_path": str(db_path),
                "vector_path": str(tmp_path / "vectors.lance"),
                "label": "mate",
                "project_root": str(tmp_path / "mate"),
            },
        ]
    }
    registry_path = tmp_path / "workspaces.json"
    import json as _json  # noqa: PLC0415

    registry_path.write_text(_json.dumps(registry_payload), encoding="utf-8")
    monkeypatch.setenv("MEMORY_WORKSPACES_FILE", str(registry_path))
    # Settings caches; reset to pick up the env override.
    import agent_memory_lite.config.settings as _settings_mod  # noqa: PLC0415

    monkeypatch.setattr(_settings_mod, "_SETTINGS", None, raising=False)

    ingest_episode(applied_conn, _episode("hub mate write", workspace_id="hub-mate"))

    report = run_integrity_audit(applied_conn, workspace_id="project-a", db_path=str(db_path))

    poll = report.checks["workspace_pollution"]
    assert poll.details["registered_foreign_rows"].get("episodes") == 1
    assert poll.details["unregistered_foreign_rows"] == {}
    assert poll.status == "ok"
    assert "workspace_pollution" not in report.failures


def test_integrity_detects_dangling_capability_link(
    applied_conn: sqlite3.Connection,
) -> None:
    skill = upsert_agent_skill(
        applied_conn,
        AgentSkillIn(
            workspace_id="project-a",
            name="Replay and backtest design",
            summary="Validate hypotheses with replay.",
        ),
    )
    theory = write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="project-a",
            title="Sparse opens are a learning bottleneck",
            claim="Admission policy may be too sparse to learn.",
        ),
    )
    link_capability(
        applied_conn,
        CapabilityLinkIn(
            workspace_id="project-a",
            target_type=CapabilityLinkTargetType.THEORY,
            target_id=theory.id,
            capability_type=CapabilityType.SKILL,
            capability_id=skill.id,
            relation=CapabilityLinkRelation.METHOD,
        ),
    )

    before = run_integrity_audit(applied_conn, workspace_id="project-a")
    assert before.checks["capability_links"].status == "ok"

    applied_conn.execute("DELETE FROM skills WHERE id = ?", (skill.id,))
    after = run_integrity_audit(applied_conn, workspace_id="project-a")

    assert after.status == "degraded"
    assert after.checks["capability_links"].details["missing_capabilities"]["skill"] == 1


def test_integrity_flags_dangling_plan_step_capability_link(
    applied_conn: sqlite3.Connection,
) -> None:
    """Phase 4: capability_links_check audits plan_step targets via the
    canonical TARGET_TABLES map — a link to a missing plan_step is
    flagged, not silently un-audited."""
    skill = upsert_agent_skill(
        applied_conn,
        AgentSkillIn(
            workspace_id="project-a",
            name="Step execution skill",
            summary="Apply when entering this plan step.",
        ),
    )
    applied_conn.execute(
        """
        INSERT INTO capability_links (
            id, workspace_id, target_type, target_id, capability_type,
            capability_id, capability_name, relation, rationale, strength,
            source_episode_id, created_at, updated_at
        ) VALUES (
            'cl_ghost', 'project-a', 'plan_step', 'ghost_step', 'skill',
            ?, 'Step execution skill', 'required_skill', NULL, 0.7,
            NULL, '2026-05-22T00:00:00Z', '2026-05-22T00:00:00Z'
        )
        """,
        (skill.id,),
    )
    applied_conn.commit()

    report = run_integrity_audit(applied_conn, workspace_id="project-a")

    assert report.status == "degraded"
    cap_check = report.checks["capability_links"]
    assert cap_check.details["missing_targets"]["plan_step"] == 1
    assert cap_check.details["missing_capabilities"] == {}  # only the target dangles


def test_integrity_warns_on_empty_workspace_manifest(
    applied_conn: sqlite3.Connection,
) -> None:
    report = run_integrity_audit(applied_conn, workspace_id="project-a")

    assert report.status == "warning"
    assert "workspace_manifest" in report.warnings


def test_integrity_accepts_workspace_manifest(
    applied_conn: sqlite3.Connection,
) -> None:
    ensure_workspace_manifest(applied_conn, workspace_id="project-a", allow_default_workspace=True)

    report = run_integrity_audit(applied_conn, workspace_id="project-a")

    assert report.checks["workspace_manifest"].status == "ok"


def test_integrity_warns_on_undisciplined_theory(
    applied_conn: sqlite3.Connection,
) -> None:
    ensure_workspace_manifest(applied_conn, workspace_id="project-a", allow_default_workspace=True)
    write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="project-a",
            title="Loose hypothesis",
            claim="A theory without validation discipline should be visible.",
            status="testing",
        ),
    )

    report = run_integrity_audit(applied_conn, workspace_id="project-a")

    assert report.status == "warning"
    assert "research_hygiene" in report.warnings
    assert report.checks["research_hygiene"].details["undisciplined_active_theories"] == 1


def test_integrity_warns_on_stale_candidate(
    applied_conn: sqlite3.Connection,
) -> None:
    ensure_workspace_manifest(applied_conn, workspace_id="project-a", allow_default_workspace=True)
    result = ingest_episode(applied_conn, _episode("candidate source"))
    applied_conn.execute(
        """
        INSERT INTO candidates (
            id, workspace_id, kind, subject, predicate, object, evidence,
            confidence, importance, trust_level, temporal_json, write_targets_json,
            metadata_json, source_episode_id, status, created_at, updated_at
        ) VALUES (
            'cand_stale', 'project-a', 'decision', 'subject', 'is', NULL, 'evidence',
            0.9, 0.9, 'agent_observed', '{}', '[]', '{}', ?, 'new',
            '2025-01-01T00:00:00+00:00', '2025-01-01T00:00:00+00:00'
        )
        """,
        (result.episode.id,),
    )

    report = run_integrity_audit(applied_conn, workspace_id="project-a")

    assert report.status == "warning"
    assert "candidate_hygiene" in report.warnings
    assert report.checks["candidate_hygiene"].details["stale_new"] == 1
