"""Translate integrity-check failures into actionable repair hints."""

from __future__ import annotations

from agent_memory_lite.maintenance.integrity_models import IntegrityCheck


def collect_repair_hints(checks: dict[str, IntegrityCheck]) -> list[str]:  # noqa: PLR0912
    hints: list[str] = []
    if checks["workspace_manifest"].status == "degraded":
        hints.append("Run migrations and verify MEMORY_WORKSPACE_ID matches this database.")
    if checks["fts"].status == "degraded":
        hints.append("Run scripts/memory_audit.py --repair-fts --backup-first.")
    vector_status = checks["vector"].status
    if vector_status == "degraded":
        if checks["vector"].details.get("extra"):
            hints.append(
                "Run scripts/memory_audit.py --rebuild-vectors-force --backup-first "
                "to drop stale vector ids and rebuild the chunk vector namespace."
            )
        elif checks["vector"].details.get("stale_embedding_hashes"):
            hints.append(
                "Run scripts/memory_audit.py --repair-vectors --backup-first to re-embed stale chunk text."
            )
        else:
            hints.append("Run scripts/memory_audit.py --repair-vectors --backup-first.")
    elif vector_status == "warning":
        metadata_status = checks["vector"].details.get("metadata_status")
        if metadata_status == "warning":
            hints.append(
                "Run scripts/memory_audit.py --repair-vectors --backup-first to stamp vector metadata."
            )
        if checks["vector"].details.get("missing_embedding_hashes"):
            hints.append(
                "Run scripts/memory_audit.py --repair-vectors --backup-first "
                "to re-embed chunks that predate embedding_text_sha256 metadata."
            )
        elif checks["vector"].details.get("missing_embedding_ids"):
            hints.append("Run scripts/memory_audit.py --repair-embedding-refs --backup-first.")
    if checks["workspace_pollution"].status == "degraded":
        hints.append(
            "Run scripts/memory_workspace_doctor.py --workspace <workspace_id> --json "
            "to inspect foreign rows; quarantine only with --quarantine --backup-first "
            "after review."
        )
    if checks["maintenance_events"].status == "degraded":
        hints.append("Inspect open maintenance_events before trusting retrieval.")
    if checks["capability_links"].status == "degraded":
        hints.append("Inspect dangling capability_links before trusting role/skill guidance.")
    if checks["code_memory_freshness"].status == "degraded":
        hints.append(
            "Run scripts/bulk_index_codebase.py --project <project_root> --workspace "
            "<workspace_id> --db-path <project_root>/.agent_memory/memory.db --force "
            "--backup-first to refresh missing or stale code-memory digests, then rerun "
            "scripts/memory_audit.py and repair vectors if it reports drift."
        )
    elif checks["code_memory_freshness"].status == "warning":
        hints.append(
            "Run scripts/bulk_index_codebase.py --project <project_root> --workspace "
            "<workspace_id> --db-path <project_root>/.agent_memory/memory.db --force "
            "--backup-first with pruning enabled to remove orphaned code-memory rows, "
            "then rerun scripts/memory_audit.py and repair vectors if it reports drift."
        )
    if checks["candidate_hygiene"].status == "warning":
        hints.append("Review or reject stale candidates; do not leave extractor output untriaged.")
    if checks["research_hygiene"].status == "warning":
        hints.append(
            "Add validation criteria, evidence, or completion state to stale research objects."
        )
    if checks["hygiene"].status == "warning":
        hints.append(
            "Run scripts/memory_hygiene.py and triage candidates, theories, experiments, "
            "insights, decisions, or capability links."
        )
    if checks["stray_dbs"].status == "warning":
        hints.append(
            "Inspect stray .agent_memory/memory.db files before trusting the selected project DB."
        )
    return hints


def collect_counts(checks: dict[str, IntegrityCheck]) -> dict[str, object]:
    return {
        "chunks": checks["fts"].details.get("chunks", 0),
        "chunks_fts": checks["fts"].details.get("chunks_fts", 0),
        "vectors": checks["vector"].details.get("vectors"),
        "missing_embedding_ids": checks["vector"].details.get("missing_embedding_ids"),
        "missing_embedding_hashes": checks["vector"].details.get("missing_embedding_hashes"),
        "stale_embedding_hashes": checks["vector"].details.get("stale_embedding_hashes"),
        "open_maintenance_events": checks["maintenance_events"].details.get("open_events"),
        "capability_links": checks["capability_links"].details.get("links"),
        "code_memory_source_files": checks["code_memory_freshness"].details.get("source_files"),
        "code_memory_missing_digests": checks["code_memory_freshness"].details.get(
            "missing_digests"
        ),
        "code_memory_stale_digests": checks["code_memory_freshness"].details.get("stale_digests"),
        "code_memory_orphaned_digests": checks["code_memory_freshness"].details.get(
            "orphaned_digests"
        ),
        "new_candidates": checks["candidate_hygiene"].details.get("new_candidates"),
        "stale_candidates": checks["candidate_hygiene"].details.get("stale_new"),
        "undisciplined_theories": checks["research_hygiene"].details.get(
            "undisciplined_active_theories"
        ),
        "stale_open_experiments": checks["research_hygiene"].details.get("stale_open_experiments"),
        "hygiene_findings": checks["hygiene"].details.get("total_findings"),
        "capability_link_warnings": len(
            checks["hygiene"].details.get("capability_link_warnings", [])
        ),
        "stray_db_warnings": len(checks["stray_dbs"].details.get("stray_dbs", [])),
        "manifest_status": checks["workspace_manifest"].status,
        "hygiene_status": checks["hygiene"].status,
        "vector_metadata_status": checks["vector"].details.get("metadata_status"),
    }
