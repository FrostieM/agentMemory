"""Enforce the project's modular-file SLOC ceiling.

Project rule (CLAUDE.md / behavior_instructions): source files under
``src/agent_memory_lite`` stay at or below 150 SLOC, one concern per
module. The ceiling drove real refactors in the early phases but
silently slipped while we were adding features. This script is the
guardrail.

Two-tier policy (so it can land without a one-shot mega-refactor):

* **Hard fail** when a file NEW to ``src/agent_memory_lite`` exceeds
  ``DEFAULT_LIMIT`` (150) SLOC. Stops further drift.
* **Warning** when a pre-existing oversized file is in the
  ``GRANDFATHERED`` set. Allows the existing oversized files to be
  decomposed in their own commits without blocking the build.

CI runs this with ``--enforce``; passing ``--list`` prints every
file's SLOC for ad-hoc inspection.

SLOC = lines that are not blank and not pure comments. Multi-line
strings count (they're real content). Conservative on purpose.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_LIMIT = 150
ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src" / "agent_memory_lite"

# Files known to exceed the cap on the day this script lands. Each one
# gets a separate decomposition commit later; until then they only
# warn. Adding NEW files to this set should require a comment about
# the planned split.
GRANDFATHERED: frozenset[str] = frozenset(
    {
        # 2.2 Move 1: settings.py is fundamentally a single concern
        # (Pydantic config class), but it has accumulated 20+ feature flags
        # across v1.0..v2.2. Decomposition into composed Settings (Embed +
        # LLM + Quality + Storage) is real work scheduled separately. Until
        # then this file is allowed to creep past the ceiling -- every new
        # flag should still be justified in code review.
        "config/settings.py",
        # Smaller pre-existing violators (150-300 SLOC). Each will get
        # a dedicated trim/split commit later. Adding NEW files here
        # should raise eyebrows in code review.
        # Files removed from this set after they were trimmed below the
        # 150-SLOC ceiling (kept here as a reminder of the decomposition
        # work already shipped):
        #   * api/routes/get_object.py
        #     -> get_object.py + get_object_payloads.py
        #   * repositories/candidates_repo.py
        #     -> candidates_repo.py + candidates_search.py
        #   * maintenance/encoding_audit.py
        #     -> encoding_audit.py + encoding_audit_models.py
        #   * ingestion/candidate_writer.py
        #     -> candidate_writer.py + candidate_promotion.py
        #   * api/schemas/research.py
        #     -> research.py + research_taxonomy.py
        #   * repositories/maintenance_repo.py
        #     -> maintenance_repo.py + maintenance_queries.py
        #   * repositories/workspace_manifest_repo.py
        #     -> workspace_manifest_repo.py + workspace_pollution_inspector.py
        #   * maintenance/benchmark.py
        #     -> benchmark.py + benchmark_runner.py
        #   * vector_store/sqlite_vec_store.py
        #     -> sqlite_vec_store.py + sqlite_vec_namespace.py
        #   * maintenance/feedback_report.py
        #     -> feedback_report.py + feedback_report_queries.py
        #   * ingestion/episode_pipeline.py
        #     -> episode_pipeline.py + episode_persist.py
        #   * models/research.py
        #     -> research.py + research_taxonomy.py
        #   * api/routes/theories.py
        #     -> theories.py + theory_responses.py + theory_list.py
        #   * maintenance/auto_triage.py
        #     -> auto_triage.py + auto_triage_models.py + auto_triage_helpers.py
        #   * api/routes/capabilities.py
        #     -> capabilities.py + capability_responses.py + capability_upsert.py
        #   * api/routes/context.py
        #     -> context.py + context_trace.py + context_explain.py
        #   * bootstrap/project_memory_seed.py
        #     -> seed.py + seed_templates.py + concepts.py
        #   * ingestion/file_pipeline.py
        #     -> file_pipeline.py + file_chunking.py + file_persist.py
        #   * repositories/capability_links_repo.py
        #     -> _repo.py + _resolver.py + _search.py
        #   * maintenance/workspace_doctor.py
        #     -> workspace_doctor.py + _internals.py + _quarantine.py
        #   * repositories/behavior_repo.py
        #     -> behavior_repo.py + _ranking.py + _search.py
        #   * repositories/theories_repo.py
        #     -> theories_repo.py + theories_search.py + theory_evidence_repo.py
        #   * maintenance/retrieval_quality.py
        #     -> retrieval_quality.py + _models.py + _runner.py + _grading.py
        #   * retrieval/explain.py
        #     -> explain.py + explain_models.py + explain_used_objects.py
        #   * api/ui_telemetry.py
        #     -> ui_telemetry.py + _event.py + _bus.py + _trace.py
        #   * maintenance/quality_gate.py
        #     -> quality_gate.py + _models.py + _checks.py + _behavior.py
        #   * repositories/capabilities_repo.py
        #     -> _repo.py (facade) + roles_repo + skills_repo + playbooks_repo
        #         + search_helpers
        #   * api/routes/research.py
        #     -> research.py + _responses + _snapshots + _experiments + _insights
        #         + _taxonomy
        #   * ingestion/research_writer.py
        #     -> research_writer.py (facade) + snapshots + experiments
        #         + experiment_create + theory_update + insights + concepts + shared
        #   * retrieval/context_builder.py
        #     -> context_builder.py (orchestrator) + _constants + _models + _text
        #         + _chunks + _intent + _gather + _pipeline + _finalize + _fitting
        #         + _fitting_eval + _fitting_finalize + _fitting_omissions
        #         + _fitting_variants + _render_main + _render_core
        #         + _render_theory + _render_research + _render_research_items
        #         + _render_behavior + _render_capabilities
        #         + _render_capabilities_items + _render_misc
        #   * mcp/stdio_server.py
        #     -> stdio_server.py (dispatcher) + stdio_runtime + stdio_env
        #         + stdio_guards + stdio_http + stdio_payloads + stdio_get_object
        #         + stdio_tools (aggregator) + stdio_tools_episodes
        #         + stdio_tools_decisions + stdio_tools_review
        #         + stdio_tools_capability + stdio_tools_theories
        #         + stdio_tools_research + stdio_tools_research_lab
        #         + stdio_tools_capabilities + stdio_handlers (dispatch table)
        #         + stdio_handlers_episodes + stdio_handlers_decisions
        #         + stdio_handlers_review + stdio_handlers_capability
        #         + stdio_handlers_theories + stdio_handlers_research
        #         + stdio_handlers_research_lists + stdio_handlers_capabilities
    }
)


def _sloc(path: Path) -> int:
    count = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        count += 1
    return count


def collect(root: Path = SRC_ROOT) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        rows.append((rel, _sloc(path)))
    return rows


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="Exit non-zero on hard violations (new oversized files).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print every file's SLOC, sorted high to low.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"SLOC ceiling (default {DEFAULT_LIMIT}).",
    )
    args = parser.parse_args(argv)

    rows = collect()
    over = [(rel, sloc) for rel, sloc in rows if sloc > args.limit]

    if args.list:
        for rel, sloc in sorted(rows, key=lambda r: -r[1]):
            print(f"{sloc:5d}  {rel}")
        return 0

    grandfathered = [(rel, sloc) for rel, sloc in over if rel in GRANDFATHERED]
    new_violations = [(rel, sloc) for rel, sloc in over if rel not in GRANDFATHERED]

    if grandfathered:
        print(f"WARN: {len(grandfathered)} grandfathered file(s) over {args.limit} SLOC:")
        for rel, sloc in sorted(grandfathered, key=lambda r: -r[1]):
            print(f"  {sloc:5d}  {rel}")
        print(
            "These need decomposition into subpackages but don't block CI. "
            "Trim them in dedicated commits.\n"
        )

    if new_violations:
        print(f"FAIL: {len(new_violations)} NEW file(s) over {args.limit} SLOC:")
        for rel, sloc in sorted(new_violations, key=lambda r: -r[1]):
            print(f"  {sloc:5d}  {rel}")
        print(
            "\nProject rule: src/agent_memory_lite/**/*.py stays at or below "
            f"{args.limit} SLOC, one concern per module. Split the file into a "
            "subpackage before adding more code."
        )
        if args.enforce:
            return 1

    if not over:
        print(f"OK: every src/agent_memory_lite/*.py is at or below {args.limit} SLOC.")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
