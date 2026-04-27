"""Eval case runner.

YAML cases drive a small in-memory exercise of the service:

- `retrieval` cases ingest one or more episodes, run `get_context`, and assert
  that the expected chunk ids appear in the top-K (recall + precision).
- `redaction` cases ingest one episode containing a known secret and assert
  the secret literal does not appear in the resulting chunk text or the
  rendered context.
- `trust_gating` cases declare a candidate and assert the trust gate matches
  the expected outcome.
"""

from __future__ import annotations

import importlib.resources
import re
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.evals.metrics import EvalReport, precision_at_k, recall_at_k
from agent_memory_lite.extraction.thresholds import meets_thresholds
from agent_memory_lite.extraction.trust_gate import passes_trust_gate
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.ingestion.research_writer import (
    distill_insight,
    register_snapshot,
    upsert_domain_concept,
    write_experiment,
)
from agent_memory_lite.ingestion.theory_writer import write_theory
from agent_memory_lite.models.candidates import MemoryCandidate, TemporalSpan
from agent_memory_lite.models.enums import EpisodeSource, MemoryCandidateKind, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.research import (
    DomainConceptIn,
    ExperimentIn,
    MemorySnapshotIn,
    ResearchInsightIn,
)
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.models.theories import TheoryIn
from agent_memory_lite.retrieval.context_builder import build_context
from agent_memory_lite.utils.time import iso_now
from agent_memory_lite.vector_store.base import VectorStore

FIXTURES_PACKAGE = "agent_memory_lite.evals.fixtures"


def _load_yaml_file(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text) or []
    if not isinstance(parsed, list):
        raise ValueError(f"{path} must contain a YAML list of eval cases")
    return parsed


def load_default_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    package_root = importlib.resources.files(FIXTURES_PACKAGE)
    yaml_entries = [path for path in package_root.iterdir() if path.name.endswith(".yaml")]
    yaml_entries.sort(key=lambda entry: entry.name)
    for entry in yaml_entries:
        with importlib.resources.as_file(entry) as concrete_path:
            cases.extend(_load_yaml_file(concrete_path))
    return cases


def _ingest_setup(
    conn: sqlite3.Connection,
    workspace_id: str,
    setup: list[dict[str, Any]],
    *,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> dict[str, str]:
    label_to_chunk: dict[str, str] = {}
    for entry in setup:
        if "episode" not in entry:
            continue
        text = str(entry["episode"])
        label = str(entry.get("label", ""))
        result = ingest_episode(
            conn,
            EpisodeIn(
                workspace_id=workspace_id,
                source_type=EpisodeSource.AGENT_ACTION,
                raw_text=text,
                trust_level=TrustLevel(entry.get("trust", TrustLevel.AGENT_OBSERVED.value)),
                importance=float(entry.get("importance", 0.6)),
            ),
            embedding_provider=embedding_provider,
            vector_store=vector_store,
        )
        if label:
            label_to_chunk[label] = result.chunk.id
    return label_to_chunk


def _build_candidate(spec: dict[str, Any]) -> MemoryCandidate:
    timestamp = iso_now()
    return MemoryCandidate(
        kind=MemoryCandidateKind(spec.get("kind", "constraint")),
        subject=str(spec.get("subject", "x")),
        predicate=str(spec.get("predicate", "is")),
        evidence=str(spec.get("evidence", "")),
        confidence=float(spec.get("confidence", 0.9)),
        importance=float(spec.get("importance", 0.85)),
        trust_level=TrustLevel(spec.get("trust_level", TrustLevel.UNKNOWN.value)),
        temporal=TemporalSpan(observed_at=timestamp, valid_from=timestamp),
        source_episode_id=spec.get("source_episode_id", "ep_synthetic"),
    )


def _run_retrieval(
    conn: sqlite3.Connection,
    case: dict[str, Any],
    workspace_id: str,
    *,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> tuple[float, float, list[str]]:
    label_map = _ingest_setup(
        conn,
        workspace_id,
        list(case.get("setup", [])),
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    query = RetrievalQuery(workspace_id=workspace_id, query=str(case["query"]))
    built = build_context(
        conn,
        query,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    retrieved_ids = [hit.id for hit in built.hits]
    expected_labels = [str(label) for label in case.get("expect_labels", [])]
    expected_ids = [label_map[label] for label in expected_labels if label in label_map]
    recall = recall_at_k(retrieved_ids, expected_ids)
    precision = precision_at_k(retrieved_ids, expected_ids)
    failures: list[str] = []
    forbidden_substrings = case.get("forbid_substrings", [])
    rendered = built.text
    for needle in forbidden_substrings:
        if str(needle) in rendered:
            failures.append(f"forbidden substring {needle!r} in context")
    return recall, precision, failures


def _seed_research_setup(
    conn: sqlite3.Connection,
    case: dict[str, Any],
    workspace_id: str,
) -> dict[str, str]:
    labels: dict[str, str] = {}
    for entry in list(case.get("setup", [])):
        _seed_research_entry(conn, entry, workspace_id=workspace_id, labels=labels)
    return labels


def _store_label(labels: dict[str, str], entry: dict[str, Any], item_id: str) -> None:
    label = str(entry.get("label", ""))
    if label:
        labels[label] = item_id


def _seed_research_entry(
    conn: sqlite3.Connection,
    entry: dict[str, Any],
    *,
    workspace_id: str,
    labels: dict[str, str],
) -> None:
    handlers = {
        "theory": _seed_theory,
        "snapshot": _seed_snapshot,
        "concept": _seed_concept,
        "experiment": _seed_experiment,
        "insight": _seed_insight,
    }
    for key, handler in handlers.items():
        if key in entry:
            item_id = handler(conn, dict(entry[key]), workspace_id=workspace_id, labels=labels)
            _store_label(labels, entry, item_id)
            return


def _seed_theory(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    workspace_id: str,
    labels: dict[str, str],
) -> str:
    del labels
    payload.setdefault("workspace_id", workspace_id)
    return write_theory(conn, TheoryIn.model_validate(payload)).id


def _seed_snapshot(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    workspace_id: str,
    labels: dict[str, str],
) -> str:
    del labels
    payload.setdefault("workspace_id", workspace_id)
    return register_snapshot(conn, MemorySnapshotIn.model_validate(payload)).id


def _seed_concept(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    workspace_id: str,
    labels: dict[str, str],
) -> str:
    del labels
    payload.setdefault("workspace_id", workspace_id)
    return upsert_domain_concept(conn, DomainConceptIn.model_validate(payload)).id


def _seed_experiment(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    workspace_id: str,
    labels: dict[str, str],
) -> str:
    payload.setdefault("workspace_id", workspace_id)
    theory_label = payload.pop("theory_label", None)
    snapshot_label = payload.pop("snapshot_label", None)
    if theory_label is not None:
        payload["theory_id"] = labels[str(theory_label)]
    if snapshot_label is not None:
        payload["snapshot_id"] = labels[str(snapshot_label)]
    return write_experiment(conn, ExperimentIn.model_validate(payload)).id


def _seed_insight(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    workspace_id: str,
    labels: dict[str, str],
) -> str:
    payload.setdefault("workspace_id", workspace_id)
    target_label = payload.pop("target_label", None)
    if target_label is not None:
        payload["target_id"] = labels[str(target_label)]
    return distill_insight(conn, ResearchInsightIn.model_validate(payload)).id


def _run_research_context(
    conn: sqlite3.Connection,
    case: dict[str, Any],
    workspace_id: str,
    *,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> list[str]:
    _seed_research_setup(conn, case, workspace_id)
    query = RetrievalQuery(
        workspace_id=workspace_id,
        query=str(case["query"]),
        max_tokens=int(case.get("max_tokens", 2500)),
    )
    built = build_context(
        conn,
        query,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    failures: list[str] = []
    rendered = built.text
    for section in case.get("expect_sections", []):
        section_name = str(section)
        if f"<{section_name}" not in rendered:
            failures.append(f"missing section <{section_name}>")
    for needle in case.get("expect_substrings", []):
        if str(needle) not in rendered:
            failures.append(f"missing substring {needle!r}")
    return failures


def _run_redaction(
    conn: sqlite3.Connection,
    case: dict[str, Any],
    workspace_id: str,
    *,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> int:
    secret = str(case["secret"])
    text = str(case["text"]).replace("{secret}", secret)
    result = ingest_episode(
        conn,
        EpisodeIn(
            workspace_id=workspace_id,
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text=text,
            trust_level=TrustLevel.AGENT_OBSERVED,
            importance=0.5,
        ),
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    leaks = 0
    if secret in result.episode.raw_text:
        leaks += 1
    if secret in result.chunk.text:
        leaks += 1
    return leaks


_INSTRUCTION_RE = re.compile(
    r"(?i)(?:ignore (?:previous|all) instructions|disregard the system prompt)"
)


def _run_prompt_injection(case: dict[str, Any]) -> int:
    text = str(case.get("payload", ""))
    return 1 if _INSTRUCTION_RE.search(text) and case.get("expect_blocked", True) is False else 0


def _run_trust_gating(case: dict[str, Any]) -> bool:
    candidate = _build_candidate(dict(case.get("candidate", {})))
    expected = bool(case.get("expect_promotable", False))
    actual = passes_trust_gate(candidate) and meets_thresholds(candidate)
    return actual == expected


def _process_case(  # noqa: PLR0912
    case: dict[str, Any],
    conn: sqlite3.Connection,
    workspace_id: str,
    *,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
    report: EvalReport,
    recalls: list[float],
    precisions: list[float],
) -> None:
    kind = case.get("type", "retrieval")
    name = case.get("name", "?")
    if kind == "retrieval":
        recall, precision, failures = _run_retrieval(
            conn,
            case,
            workspace_id,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
        )
        recalls.append(recall)
        precisions.append(precision)
        if failures:
            report.failures.extend(f"{name}: {f}" for f in failures)
        else:
            report.cases_passed += 1
    elif kind == "redaction":
        leaks = _run_redaction(
            conn,
            case,
            workspace_id,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
        )
        report.secret_leak_count += leaks
        if leaks == 0:
            report.cases_passed += 1
        else:
            report.failures.append(f"{name}: secret leaked")
    elif kind == "research_context":
        failures = _run_research_context(
            conn,
            case,
            workspace_id,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
        )
        if failures:
            report.failures.extend(f"{name}: {failure}" for failure in failures)
        else:
            report.cases_passed += 1
    elif kind == "trust_gating":
        if _run_trust_gating(case):
            report.cases_passed += 1
        else:
            report.failures.append(f"{name}: trust gating outcome mismatched")
    elif kind == "prompt_injection":
        failures_int = _run_prompt_injection(case)
        report.prompt_injection_failures += failures_int
        if failures_int == 0:
            report.cases_passed += 1
        else:
            report.failures.append(f"{name}: prompt injection slipped")
    else:
        report.failures.append(f"{name}: unknown case type {kind!r}")


def run_evals(
    conn_factory: Callable[[], AbstractContextManager[sqlite3.Connection]],
    *,
    workspace_id: str,
    cases: Iterable[dict[str, Any]] | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
) -> EvalReport:
    materialized = list(cases) if cases is not None else load_default_cases()
    report = EvalReport()
    recalls: list[float] = []
    precisions: list[float] = []

    for case in materialized:
        report.cases_run += 1
        try:
            with conn_factory() as conn:
                _process_case(
                    case,
                    conn,
                    workspace_id,
                    embedding_provider=embedding_provider,
                    vector_store=vector_store,
                    report=report,
                    recalls=recalls,
                    precisions=precisions,
                )
        except Exception as exc:
            report.failures.append(f"{case.get('name', '?')}: error {exc!s}")

    if recalls:
        report.retrieval_recall_at_10 = sum(recalls) / len(recalls)
    if precisions:
        report.retrieval_precision_at_10 = sum(precisions) / len(precisions)
    return report
