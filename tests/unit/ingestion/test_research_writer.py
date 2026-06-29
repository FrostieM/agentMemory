from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.research_writer import (
    add_experiment_result,
    distill_insight,
    register_snapshot,
    update_insight,
    upsert_domain_concept,
    write_experiment,
)
from agent_memory_lite.ingestion.theory_writer import write_theory
from agent_memory_lite.models.enums import (
    ConceptKind,
    ExperimentStatus,
    InsightStatus,
    InsightType,
    TheoryEvidenceKind,
    TheoryStatus,
)
from agent_memory_lite.models.research import (
    DomainConceptIn,
    ExperimentIn,
    ExperimentResultIn,
    MemorySnapshotIn,
    ResearchInsightIn,
    ResearchInsightUpdateIn,
)
from agent_memory_lite.models.theories import TheoryIn
from agent_memory_lite.redaction.redactor import REDACTION_MARKER_PREFIX
from agent_memory_lite.repositories.research_repo import (
    get_experiment,
    list_concepts,
    list_experiment_results,
    list_insights,
)
from agent_memory_lite.repositories.theories_repo import get_theory, list_evidence_for_theory

# A secret the redactor reliably catches. It must never survive in any persisted
# column NOR in the durable_fts BM25 index for the research-route write paths
# (concept/insight) -- those business writers bypass write_canonical's redaction.
_SECRET_RAW = "sk-ant-secret-LEAK-RESEARCH"
_SECRET = f"api_key: {_SECRET_RAW}"


def _fts_content(conn: sqlite3.Connection, object_id: str) -> str:
    row = conn.execute(
        "SELECT content FROM durable_fts WHERE object_id = ?", (object_id,)
    ).fetchone()
    return str(row[0] or "") if row is not None else ""


def test_distill_insight_redacts_secret_on_disk_and_in_fts(
    applied_conn: sqlite3.Connection,
) -> None:
    """R6 audit: distill_insight (POST /memory/distill_insight + candidate promotion)
    previously did NOT redact, so a pasted secret landed cleartext on disk -- and
    once the path started syncing durable_fts it would surface in memory_search.
    The secret must appear in NEITHER the stored row NOR the FTS index."""
    insight = distill_insight(
        applied_conn,
        ResearchInsightIn(
            workspace_id="default",
            insight_type=InsightType.RISK,
            summary=_SECRET,
            proposed_action=f"rotate {_SECRET}",
            confidence=0.8,
        ),
    )
    assert _SECRET_RAW not in insight.summary
    assert _SECRET_RAW not in (insight.proposed_action or "")
    assert _SECRET_RAW not in _fts_content(applied_conn, insight.id)


def test_upsert_domain_concept_redacts_secret_in_name(
    applied_conn: sqlite3.Connection,
) -> None:
    """Certification finding: `name` is an FTS-indexed column for concept, and Batch
    B's new sync surfaces it into durable_fts. A secret pasted into the concept name
    (via POST /memory/upsert_concept) must not survive in concepts.name NOR the FTS
    index, and must not be retrievable by the secret token. The redacted name stays a
    STABLE upsert key (re-upsert maps to the same row)."""
    from agent_memory_lite.storage.reader import search_kind_fts  # noqa: PLC0415

    concept = upsert_domain_concept(
        applied_conn,
        DomainConceptIn(
            workspace_id="default",
            name=f"leaky {_SECRET}",
            kind=ConceptKind.METRIC,
            definition="a clean definition",
        ),
    )
    assert _SECRET_RAW not in concept.name
    assert _SECRET_RAW not in _fts_content(applied_conn, concept.id)
    hits = search_kind_fts(
        applied_conn, workspace_id="default", kind="concept", query=_SECRET_RAW, limit=5
    )
    assert concept.id not in [h.projection["id"] for h in hits]
    # The redacted name is deterministic, so a re-upsert keys to the SAME row.
    again = upsert_domain_concept(
        applied_conn,
        DomainConceptIn(
            workspace_id="default",
            name=f"leaky {_SECRET}",
            kind=ConceptKind.METRIC,
            definition="an updated clean definition",
        ),
    )
    assert again.id == concept.id


def test_upsert_domain_concept_redacts_secret_in_tags(
    applied_conn: sqlite3.Connection,
) -> None:
    """Certification finding: `tags` was omitted from redaction. A secret in a tag
    lands cleartext in concepts.tags_json (retrievable via memory_get) -- the blessed
    write_canonical/redact_freetext_fields path redacts tags, so this must too."""
    concept = upsert_domain_concept(
        applied_conn,
        DomainConceptIn(
            workspace_id="default",
            name="clean-concept",
            kind=ConceptKind.METRIC,
            definition="a clean definition",
            tags=[_SECRET, "ordinary"],
        ),
    )
    row = applied_conn.execute(
        "SELECT tags_json FROM concepts WHERE id=?", (concept.id,)
    ).fetchone()
    assert _SECRET_RAW not in (row[0] or "")
    assert "ordinary" in (row[0] or "")  # non-secret tag preserved


def test_distill_insight_redacts_secret_in_tags(applied_conn: sqlite3.Connection) -> None:
    """Certification finding: `tags` was omitted from redaction in distill_insight."""
    insight = distill_insight(
        applied_conn,
        ResearchInsightIn(
            workspace_id="default",
            insight_type=InsightType.RISK,
            summary="a clean summary",
            confidence=0.8,
            tags=[_SECRET, "ordinary"],
        ),
    )
    row = applied_conn.execute(
        "SELECT tags_json FROM insights WHERE id=?", (insight.id,)
    ).fetchone()
    assert _SECRET_RAW not in (row[0] or "")
    assert "ordinary" in (row[0] or "")


def test_upsert_domain_concept_redacts_secret_on_disk_and_in_fts(
    applied_conn: sqlite3.Connection,
) -> None:
    """R6 audit: upsert_domain_concept (POST /memory/upsert_concept + seeding)
    previously did NOT redact. Secret must not survive in definition/aliases or FTS."""
    concept = upsert_domain_concept(
        applied_conn,
        DomainConceptIn(
            workspace_id="default",
            name="leaky-concept",
            kind=ConceptKind.METRIC,
            definition=_SECRET,
            aliases=[_SECRET, "ordinary"],
        ),
    )
    assert _SECRET_RAW not in concept.definition
    assert _SECRET_RAW not in " ".join(concept.aliases)
    assert "ordinary" in concept.aliases  # non-secret alias preserved
    assert _SECRET_RAW not in _fts_content(applied_conn, concept.id)


def test_experiment_result_summary_is_redacted_in_every_sink(
    applied_conn: sqlite3.Connection,
) -> None:
    """A secret in a result summary must not land cleartext anywhere.

    add_experiment_result fans the summary into THREE tables: experiment_results,
    theory_evidence, and (for a refuting result) the contradiction insight. Redact
    once at the entry point and thread the safe value to all three -- before this
    fix the experiment_results row leaked the raw secret. The contradiction insight
    is also FTS-indexed, so the secret must be absent from durable_fts.content too.
    """
    secret = "result api_key: sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGG1234567890"
    raw_token = "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGG1234567890"

    theory = write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="default",
            title="Secrets must never be stored",
            claim="A pasted credential must be redacted before persistence.",
            status=TheoryStatus.SUPPORTED,
            confidence=0.72,
        ),
    )
    experiment = write_experiment(
        applied_conn,
        ExperimentIn(
            workspace_id="default",
            theory_id=theory.id,
            title="Paste a credential into a result summary",
            hypothesis="The summary should be redacted in every table.",
        ),
    )

    result = add_experiment_result(
        applied_conn,
        ExperimentResultIn(
            workspace_id="default",
            experiment_id=experiment.id,
            kind=TheoryEvidenceKind.REFUTING,
            summary=secret,
            confidence=0.75,
        ),
    )

    # experiment_results row (the sink my Batch B fix missed; quirky catches it).
    assert raw_token not in result.summary
    assert REDACTION_MARKER_PREFIX in result.summary
    stored = list_experiment_results(applied_conn, workspace_id="default")
    assert stored
    assert raw_token not in stored[0].summary
    assert REDACTION_MARKER_PREFIX in stored[0].summary

    # theory_evidence row (the sibling path, which also leaked).
    evidence = list_evidence_for_theory(applied_conn, theory.id)
    assert evidence
    assert raw_token not in evidence[0].summary
    assert REDACTION_MARKER_PREFIX in evidence[0].summary

    # contradiction insight (refuting + high confidence => emitted) + its FTS index.
    insights = list_insights(
        applied_conn,
        workspace_id="default",
        statuses=[InsightStatus.NEW],
    )
    assert insights
    assert insights[0].insight_type is InsightType.CONTRADICTION
    assert raw_token not in insights[0].summary
    assert REDACTION_MARKER_PREFIX in insights[0].summary
    assert raw_token not in _fts_content(applied_conn, insights[0].id)


def test_snapshot_experiment_result_updates_theory_confidence(
    applied_conn: sqlite3.Connection,
) -> None:
    theory = write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="default",
            title="Source-flip favorites",
            claim="Tennis favorite source-flips may have edge.",
            status=TheoryStatus.PROPOSED,
            confidence=0.4,
        ),
    )
    snapshot = register_snapshot(
        applied_conn,
        MemorySnapshotIn(
            workspace_id="default",
            snapshot_key="server_20260427T105823",
            title="VPS snapshot before reset",
            duckdb_path="research/snapshots/server_20260427T105823/research.duckdb",
            table_counts={"bot_trade_log": 226057},
            total_rows=499141,
        ),
    )
    experiment = write_experiment(
        applied_conn,
        ExperimentIn(
            workspace_id="default",
            theory_id=theory.id,
            snapshot_id=snapshot.id,
            title="Replay favorite source-flips",
            hypothesis="Favorites outperform underdogs after source-flip.",
            success_criteria={"min_trades": 100, "net_edge_bps_gt": 0},
            priority=0.9,
        ),
    )

    result = add_experiment_result(
        applied_conn,
        ExperimentResultIn(
            workspace_id="default",
            experiment_id=experiment.id,
            kind=TheoryEvidenceKind.SUPPORTING,
            summary="Initial replay supports the favorite-only cohort.",
            metrics={"trades": 144, "net_edge_bps": 31.2},
            confidence=0.8,
            artifact_path="reports/research/source_flip_favorites.md",
        ),
    )

    stored_experiment = get_experiment(applied_conn, experiment.id)
    assert stored_experiment is not None
    assert stored_experiment.status is ExperimentStatus.COMPLETED
    assert result.metrics["experiment_id"] == experiment.id
    updated_theory = get_theory(applied_conn, theory.id)
    assert updated_theory is not None
    assert updated_theory.confidence > theory.confidence
    assert updated_theory.status is TheoryStatus.TESTING
    evidence = list_evidence_for_theory(applied_conn, theory.id)
    assert evidence[0].summary == "Initial replay supports the favorite-only cohort."
    assert evidence[0].metrics["experiment_result_id"] == result.id


def test_refuting_result_creates_contradiction_insight(applied_conn: sqlite3.Connection) -> None:
    theory = write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="default",
            title="Sparse opens are only a runtime bug",
            claim="Paper opens are sparse because the trader path is broken.",
            status=TheoryStatus.SUPPORTED,
            confidence=0.72,
        ),
    )
    experiment = write_experiment(
        applied_conn,
        ExperimentIn(
            workspace_id="default",
            theory_id=theory.id,
            title="Admission-gate audit",
            hypothesis="Selector/admission gates explain sparse opens.",
        ),
    )

    add_experiment_result(
        applied_conn,
        ExperimentResultIn(
            workspace_id="default",
            experiment_id=experiment.id,
            kind=TheoryEvidenceKind.REFUTING,
            summary="Trader path is healthy; selector gates block most candidates.",
            metrics={"queue_selected": 0, "paper_opened": 0},
            confidence=0.75,
        ),
    )

    updated = get_theory(applied_conn, theory.id)
    assert updated is not None
    assert updated.status is TheoryStatus.WEAKENED
    insights = list_insights(
        applied_conn,
        workspace_id="default",
        query="selector gates",
        statuses=[InsightStatus.NEW],
    )
    assert insights
    assert insights[0].insight_type.value == "contradiction"
    assert insights[0].target_id == theory.id


def test_contradiction_insight_from_experiment_is_fts_searchable(
    applied_conn: sqlite3.Connection,
) -> None:
    """M1 (write-atomicity batch): a REFUTING experiment result writes a
    contradiction insight via insert_insight_row inside update_theory_after_result,
    bypassing write_canonical's FTS sync (reached from the record_experiment_result
    route). Without the new sync the insight's searchable summary was invisible to
    memory_search. It must be searchable immediately after the result lands."""
    from agent_memory_lite.storage.reader import search_kind_fts  # noqa: PLC0415

    theory = write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="default",
            title="Mongoose latency is purely network-bound",
            claim="All mongoose latency comes from the network hop.",
            status=TheoryStatus.SUPPORTED,
            confidence=0.7,
        ),
    )
    experiment = write_experiment(
        applied_conn,
        ExperimentIn(
            workspace_id="default",
            theory_id=theory.id,
            title="Mongoose CPU-profile audit",
            hypothesis="CPU profiling explains the mongoose latency.",
        ),
    )
    add_experiment_result(
        applied_conn,
        ExperimentResultIn(
            workspace_id="default",
            experiment_id=experiment.id,
            kind=TheoryEvidenceKind.REFUTING,
            summary="Profiling shows a mongoose serialization hotspot, not the network.",
            metrics={"cpu_pct": 71},
            confidence=0.8,
        ),
    )
    insight_id = applied_conn.execute(
        "SELECT id FROM insights WHERE workspace_id='default' AND target_id=? "
        "AND insight_type='contradiction'",
        (theory.id,),
    ).fetchone()[0]
    hits = search_kind_fts(
        applied_conn,
        workspace_id="default",
        kind="insight",
        query="mongoose serialization",
        limit=5,
    )
    # The contradiction insight itself (not merely "some hit") must be findable.
    assert insight_id in [h.projection["id"] for h in hits]


def test_concept_upsert_reuses_name_per_workspace(applied_conn: sqlite3.Connection) -> None:
    first = upsert_domain_concept(
        applied_conn,
        DomainConceptIn(
            workspace_id="default",
            name="paper-open-rate",
            kind=ConceptKind.METRIC,
            definition="Share of selected candidates that become paper positions.",
            tags=["trading-bot", "paper"],
        ),
    )
    second = upsert_domain_concept(
        applied_conn,
        DomainConceptIn(
            workspace_id="default",
            name="paper-open-rate",
            kind=ConceptKind.METRIC,
            definition="Paper positions opened divided by selector-approved candidates.",
            aliases=["open-rate"],
            confidence=0.9,
        ),
    )

    assert second.id == first.id
    concepts = list_concepts(applied_conn, workspace_id="default", query="open-rate")
    assert [concept.id for concept in concepts] == [first.id]
    assert (
        concepts[0].definition == "Paper positions opened divided by selector-approved candidates."
    )


def test_distilled_insight_is_fts_searchable(applied_conn: sqlite3.Connection) -> None:
    """M1 (write-atomicity batch): distill_insight INSERTs the insight row
    directly, bypassing write_canonical's FTS sync choke point. Before M1 the
    distilled insight (a DURABLE_FTS_KIND) was invisible to memory_search until
    the brain-pass rebuild backstop re-indexed it. It must be searchable the
    instant the distill commits."""
    from agent_memory_lite.storage.reader import search_kind_fts  # noqa: PLC0415

    insight = distill_insight(
        applied_conn,
        ResearchInsightIn(
            workspace_id="default",
            insight_type=InsightType.RISK,
            summary="Always reconcile the platypus ledger before the nightly batch.",
            proposed_action="Add a platypus reconciliation gate.",
            confidence=0.8,
        ),
    )
    hits = search_kind_fts(
        applied_conn, workspace_id="default", kind="insight", query="platypus ledger", limit=5
    )
    assert insight.id in [h.projection["id"] for h in hits]


def test_upserted_concept_is_fts_searchable(applied_conn: sqlite3.Connection) -> None:
    """M1 (write-atomicity batch): upsert_domain_concept (research-taxonomy HTTP
    route + project seeding) upserts the concept directly, bypassing
    write_canonical's FTS sync choke point. Before M1 the concept (a
    DURABLE_FTS_KIND -- name + definition indexed) was invisible to memory_search.
    It must be searchable immediately, and the re-index must survive an UPSERT that
    reuses the existing row id (the sync must target stored.id, not a fresh id)."""
    from agent_memory_lite.storage.reader import search_kind_fts  # noqa: PLC0415

    first = upsert_domain_concept(
        applied_conn,
        DomainConceptIn(
            workspace_id="default",
            name="dugong-throughput",
            kind=ConceptKind.METRIC,
            definition="Rate at which the dugong pipeline drains its queue.",
        ),
    )
    hits = search_kind_fts(
        applied_conn, workspace_id="default", kind="concept", query="dugong throughput", limit=5
    )
    assert first.id in [h.projection["id"] for h in hits]

    # An UPSERT on the same name reuses first.id and rewrites the indexed
    # definition; the re-sync must keep the (single) row findable by new text.
    second = upsert_domain_concept(
        applied_conn,
        DomainConceptIn(
            workspace_id="default",
            name="dugong-throughput",
            kind=ConceptKind.METRIC,
            definition="Manatee-adjusted drain rate of the dugong pipeline queue.",
        ),
    )
    assert second.id == first.id
    hits2 = search_kind_fts(
        applied_conn, workspace_id="default", kind="concept", query="manatee drain", limit=5
    )
    assert second.id in [h.projection["id"] for h in hits2]


def test_update_insight_links_existing_research_item(applied_conn: sqlite3.Connection) -> None:
    insight = distill_insight(
        applied_conn,
        ResearchInsightIn(
            workspace_id="default",
            insight_type=InsightType.RISK,
            summary="Insight exists but has not been linked into the reasoning graph.",
            proposed_action="Attach the insight to the theory it supports.",
            confidence=0.8,
        ),
    )

    updated = update_insight(
        applied_conn,
        ResearchInsightUpdateIn(
            workspace_id="default",
            insight_id=insight.id,
            target_type="theory",
            target_id="th_example",
            status=InsightStatus.ACCEPTED,
        ),
    )

    assert updated.id == insight.id
    assert updated.target_type == "theory"
    assert updated.target_id == "th_example"
    assert updated.status is InsightStatus.ACCEPTED
    insights = list_insights(applied_conn, workspace_id="default", query="th_example")
    assert [item.id for item in insights] == [insight.id]
