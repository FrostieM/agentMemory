"""Grading helpers for the retrieval-quality eval.

Split out of ``retrieval_quality_runner.py`` so each module stays
under the SLOC ceiling. Builds the failure list a runner attaches to
``RetrievalQualityResult``.
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.maintenance.retrieval_quality_models import RetrievalQualityCase

_RENDER_LEVEL_RANK = {"none": 0, "stub": 1, "summary": 2, "full": 3}


def render_rank(level: str | None) -> int:
    return _RENDER_LEVEL_RANK.get(str(level or "none"), 0)


def render_levels_from_diagnostics(diagnostics: dict[str, Any]) -> dict[str, str]:
    sections = diagnostics.get("sections", [])
    if not isinstance(sections, list):
        return {}
    levels: dict[str, str] = {}
    for item in sections:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if name:
            levels[name] = str(item.get("render_level") or "none")
    return levels


def _check_sources(
    case: RetrievalQualityCase,
    matched_ids: list[str],
    source_map: dict[str, list[str]],
) -> list[str]:
    failures: list[str] = []
    for expected_source in case.expected_sources:
        source_ok = False
        if expected_source == "both":
            source_ok = any(
                {"fts", "vector"}.issubset(set(source_map.get(expected_id, [])))
                for expected_id in matched_ids
            )
        if case.expected_ids:
            source_ok = source_ok or any(
                expected_source in source_map.get(expected_id, []) for expected_id in matched_ids
            )
        else:
            source_ok = any(expected_source in sources for sources in source_map.values())
        if not source_ok:
            failures.append(f"missing expected retrieval source {expected_source!r}")
    return failures


def grade_failures(
    *,
    case: RetrievalQualityCase,
    retrieved_ids: list[str],
    matched_ids: list[str],
    matched_context_ids: list[str],
    matched_object_titles: list[str],
    source_map: dict[str, list[str]],
    render_levels: dict[str, str],
    built_text: str,
    budget_diagnostics: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    missing_ids = [item for item in case.expected_ids if item not in retrieved_ids]
    if missing_ids:
        failures.append(f"missing expected ids in top {case.top_k}: {missing_ids}")
    missing_context_ids = [
        item for item in case.expected_context_ids if item not in matched_context_ids
    ]
    if missing_context_ids:
        failures.append(f"missing expected context ids: {missing_context_ids}")
    failures.extend(_check_sources(case, matched_ids, source_map))
    for section in case.expected_sections:
        if f"<{section}" not in built_text:
            failures.append(f"missing context section <{section}>")
    missing_titles = [
        item for item in case.expected_object_titles if item not in matched_object_titles
    ]
    if missing_titles:
        failures.append(f"missing expected object titles: {missing_titles}")
    if case.min_render_level:
        checked_sections = case.expected_sections or list(render_levels)
        weak_sections = [
            section
            for section in checked_sections
            if render_rank(render_levels.get(section)) < render_rank(case.min_render_level)
        ]
        if weak_sections:
            failures.append(f"sections below render level {case.min_render_level}: {weak_sections}")
    if case.expected_omissions_absent and budget_diagnostics.get("omissions"):
        failures.append("unexpected context omissions under sentinel budget")
    for needle in case.expected_substrings:
        if needle not in built_text:
            failures.append(f"missing context substring {needle!r}")
    return failures
