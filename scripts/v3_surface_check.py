"""Fail if active files reintroduce legacy memory surfaces."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

_SKIP_DIRS = {
    ".agent_memory",
    # .claude holds gitignored agent-local state, incl. nested git WORKTREES
    # (.claude/worktrees/<name>/ = a full checkout of another branch). Those are
    # untracked and absent from a clean CI checkout, so scanning them lints a
    # SIBLING branch's source and emits phantom violations -- skip the whole tree.
    ".claude",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "reports",
}
_SKIP_FILES = {
    Path("CHANGELOG.md"),
    Path("SESSION_STATE.md"),
    Path("scripts/memory_contract_check.py"),
    Path("scripts/_setup_doctor.py"),
    Path("scripts/v3_surface_check.py"),
    Path("tests/e2e/test_v3_active_surface_routes.py"),
    Path("tests/unit/scripts/test_memory_contract_check.py"),
    Path("tests/unit/scripts/test_setup_doctor.py"),
    Path("tests/unit/scripts/test_v3_surface_check.py"),
}
_SKIP_PREFIXES = (Path("docs/archive"),)
_TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".sh",
    ".yml",
    ".yaml",
    ".html",
    ".js",
    ".ts",
    ".json",
    ".sql",
    ".toml",
}
_LEGACY_HTTP_PATTERNS = [
    r'["\']?/memory/capability/record_outcome["\']?',
    r'["\']?/memory/(?:write_decision|list_decisions|upsert_behavior_instruction|list_behavior_instructions|update_task_state|get_context|list_agent_capabilities)["\']?',
    r'["\']?/memory/(?:find_symbols|graph_neighbors|symbol_history|breaking_changes|soft_neighbors|code_overview|code_graph)["\']?',
    r'["\']?/ui/(?:code|graph)(?:\.html)?["\']?',
    r"\bmemory_(?:write_decision|list_decisions|upsert_behavior_instruction|update_task_state|get_context|list_agent_capabilities)\b",
    r"\bmemory_(?:find_symbols|graph_neighbors|symbol_history|breaking_changes|soft_neighbors|code_overview|code_graph|claim_edit|release_edit|list_active_edits)\b",
]
_DELETED_MODULE_PATTERNS = [
    r"\bagent_memory_lite\.api\.routes\.(?:behavior|decisions|task_state|archive|pin|search|record_compound|theory_list|active_edits|file_digests|capability_links|capability_upsert|capability_responses|decision_candidates|decision_candidate_actions|insight_candidates|insight_candidate_actions|memory_state_snapshots|find_symbols|graph_neighbors|symbol_history|breaking_changes|soft_neighbors|code_overview|code_graph)\b",
    r"\bfrom\s+agent_memory_lite\.api\.routes\s+import\s+[^\n#]*\b(?:find_symbols|graph_neighbors|symbol_history|breaking_changes|soft_neighbors|code_overview|code_graph)\b",
    r"\bagent_memory_lite\.api\.routes\.(?:capabilities|candidates)\b",
    r"\bagent_memory_lite\.api\.schemas\.(?:capabilities|candidates|decision_candidates|insight_candidates|memory_state_snapshots)\b",
    r"\bagent_memory_lite\.(?:models|repositories)\.(?:active_edits|active_edits_repo)\b",
    r"\bagent_memory_lite\.(?:models|repositories)\.(?:memory_state_snapshots|memory_state_snapshots_repo)\b",
    r"\bagent_memory_lite\.ingestion\.memory_state_snapshot_(?:writer|diff)\b",
    r"\bfrom\s+agent_memory_lite\.api\.routes\s+import\s+[^\n#]*\btheory_list\b",
    r"\bagent_memory_lite\.api\.schemas\.(?:behavior|decisions|task_state|archive|pin|search|record_compound)\b",
    r"\bagent_memory_lite\.mcp\.(?:v2_compat|stdio_http|stdio_payloads)\b",
    r"\bagent_memory_lite\.mcp\.(?:stdio_handlers|stdio_tools|tools)_(?:archive|capabilities|decisions|episodes|research|theories|v3_1)\b",
]
_V2_TABLES = {
    "agent_roles",
    "agent_skills",
    "agent_playbooks",
    "active_edits",
    "behavior_instructions",
    "core_memory",
    "decision_candidates",
    "procedural_rules",
    "task_state",
    "domain_concepts",
    "insight_candidates",
    "memory_state_snapshots",
    "research_insights",
}
_ACTIVE_SURFACE_FILES = {
    Path("src/agent_memory_lite/api/app_routes.py"),
    Path("src/agent_memory_lite/api/routes/memory.py"),
    Path("src/agent_memory_lite/api/routes/research.py"),
    Path("src/agent_memory_lite/api/routes/research_taxonomy.py"),
    Path("src/agent_memory_lite/mcp/stdio_handlers.py"),
    Path("src/agent_memory_lite/mcp/stdio_handlers_memory.py"),
    Path("src/agent_memory_lite/mcp/stdio_server.py"),
    Path("src/agent_memory_lite/mcp/stdio_tools.py"),
    Path("src/agent_memory_lite/mcp/stdio_tools_memory.py"),
}
_STATUS_CONTRACT_FILES = {
    Path("scripts/memory_mcp_smoke.py"),
    Path("src/agent_memory_lite/api/routes/memory_status_queries.py"),
    Path("src/agent_memory_lite/api/schemas/memory_status.py"),
    Path("src/agent_memory_lite/ui/metrics.html"),
}
_CONFIG_CONTRACT_FILES = {
    Path("pyproject.toml"),
}
_BOOTSTRAP_CONTRACT_FILES = {
    Path("src/agent_memory_lite/bootstrap/project_memory_seed_behavior.py"),
    Path("src/agent_memory_lite/bootstrap/project_memory_seed_behavior_post_write.py"),
    Path("src/agent_memory_lite/bootstrap/project_memory_seed_behavior_writes.py"),
    Path("src/agent_memory_lite/bootstrap/project_memory_seed_templates.py"),
}
_FORBIDDEN_ACTIVE_LEGACY_MODULES = {
    Path("src/agent_memory_lite/ingestion/core_memory_writer.py"),
    Path("src/agent_memory_lite/ingestion/procedural_writer.py"),
    Path("src/agent_memory_lite/models/core_memory.py"),
    Path("src/agent_memory_lite/models/procedural.py"),
    Path("src/agent_memory_lite/repositories/core_memory_repo.py"),
    Path("src/agent_memory_lite/repositories/procedural_repo.py"),
}
_FORBIDDEN_ACTIVE_PATTERNS = [
    r"\b(?:candidates|capability_links|theories|active_edits|file_digests)\b",
    r'["\']/memory/capability/record_outcome["\']',
    r'["\']/memory/(?:list|count|rollback|versions|write_theory|list_theories|list_research_agenda|list_concepts|list_insights|link_capability|list_capability_links|list_candidates|list_audit|list_disputes|list_maintenance_events|list_active_edits|list_file_digests|file_digest|claim_edit|release_edit|find_symbols|graph_neighbors|symbol_history|breaking_changes|soft_neighbors|code_overview|code_graph)["\']',
    r'["\']/ui/(?:code|graph)(?:\.html)?["\']',
    r'["\']/memory/list_\w+["\']',
    r'@router\.(?:get|post|put|patch|delete)\(["\']/(?:list|count|rollback|versions|list_research_agenda|list_concepts|list_insights)["\']',
    r'@router\.(?:get|post|put|patch|delete)\(["\']/memory/list_(?:research_agenda|concepts|insights)["\']',
    r"\bmemory_(?:get_context|get_object|list_\w+|write_decision|record_with_evidence)\b",
    r"\binject_memory_context\.py\b",
]
_FORBIDDEN_STATUS_CONTRACT_PATTERNS = [
    r"\bbehavior_instructions_(?:active|total|fired_ratio)\b",
]
_FORBIDDEN_CONFIG_CONTRACT_PATTERNS = [
    r"\binject_memory_context\.py\b",
]
_FORBIDDEN_BOOTSTRAP_PATTERNS = [
    r"\bmemory_link_capability\b",
    r"\blink_capability_discipline_instruction\b",
    r"\bLink capability after every decision and theory write\b",
]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check active v3-only surface invariants.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--json", action="store_true")
    return parser


def _is_skipped(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if rel in _SKIP_FILES:
        return True
    return any(rel == prefix or prefix in rel.parents for prefix in _SKIP_PREFIXES)


def _iter_text_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for path in root.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        if _is_skipped(path, root):
            continue
        out.append(path)
    return sorted(out)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _scan_text(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    patterns = [
        ("legacy_http_or_tool_surface", re.compile(item)) for item in _LEGACY_HTTP_PATTERNS
    ] + [("deleted_module_reference", re.compile(item)) for item in _DELETED_MODULE_PATTERNS]
    for path in _iter_text_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(root))
        for kind, pattern in patterns:
            for match in pattern.finditer(text):
                findings.append(
                    {
                        "kind": kind,
                        "path": rel,
                        "line": _line_number(text, match.start()),
                        "match": match.group(0),
                    }
                )
    return findings


def _check_active_surface(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    patterns = [re.compile(item) for item in _FORBIDDEN_ACTIVE_PATTERNS]
    for rel_path in sorted(_ACTIVE_SURFACE_FILES):
        path = root / rel_path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in patterns:
            for match in pattern.finditer(text):
                findings.append(
                    {
                        "kind": "forbidden_active_surface",
                        "path": str(rel_path),
                        "line": _line_number(text, match.start()),
                        "match": match.group(0),
                    }
                )
    return findings


def _check_bootstrap_contract(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    patterns = [re.compile(item) for item in _FORBIDDEN_BOOTSTRAP_PATTERNS]
    for rel_path in sorted(_BOOTSTRAP_CONTRACT_FILES):
        path = root / rel_path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in patterns:
            for match in pattern.finditer(text):
                findings.append(
                    {
                        "kind": "forbidden_bootstrap_contract",
                        "path": str(rel_path),
                        "line": _line_number(text, match.start()),
                        "match": match.group(0),
                    }
                )
    return findings


def _check_status_contract(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    patterns = [re.compile(item) for item in _FORBIDDEN_STATUS_CONTRACT_PATTERNS]
    for rel_path in sorted(_STATUS_CONTRACT_FILES):
        path = root / rel_path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in patterns:
            for match in pattern.finditer(text):
                findings.append(
                    {
                        "kind": "forbidden_status_contract",
                        "path": str(rel_path),
                        "line": _line_number(text, match.start()),
                        "match": match.group(0),
                    }
                )
    return findings


def _check_config_contract(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    patterns = [re.compile(item) for item in _FORBIDDEN_CONFIG_CONTRACT_PATTERNS]
    for rel_path in sorted(_CONFIG_CONTRACT_FILES):
        path = root / rel_path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in patterns:
            for match in pattern.finditer(text):
                findings.append(
                    {
                        "kind": "forbidden_config_contract",
                        "path": str(rel_path),
                        "line": _line_number(text, match.start()),
                        "match": match.group(0),
                    }
                )
    return findings


def _check_removed_legacy_modules(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for rel_path in sorted(_FORBIDDEN_ACTIVE_LEGACY_MODULES):
        if (root / rel_path).exists():
            findings.append(
                {
                    "kind": "forbidden_active_legacy_module",
                    "path": str(rel_path),
                    "line": 0,
                    "match": rel_path.name,
                }
            )
    return findings


def _check_migrations(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    migration_dir = root / "migrations"
    sql_files = sorted(p.name for p in migration_dir.glob("*.sql"))
    # The v3 baseline is the squashed 0001_init.sql. Forward-only numbered
    # migrations on top of it (0002_*, 0003_*, ...) are the SUPPORTED shape, not
    # a defect -- so flag only a missing baseline or a filename that is not a
    # NNNN_<slug>.sql forward migration (the runner enforces the same pattern).
    if "0001_init.sql" not in sql_files:
        findings.append(
            {
                "kind": "missing_squashed_baseline",
                "path": "migrations",
                "line": 0,
                "match": ", ".join(sql_files) or "(no .sql files)",
            }
        )
    nonconforming = [n for n in sql_files if not re.match(r"^\d{4}_[A-Za-z0-9_]+\.sql$", n)]
    if nonconforming:
        findings.append(
            {
                "kind": "nonconforming_migration_filename",
                "path": "migrations",
                "line": 0,
                "match": ", ".join(nonconforming),
            }
        )
    init = migration_dir / "0001_init.sql"
    if init.exists():
        text = init.read_text(encoding="utf-8", errors="replace")
        for table in sorted(_V2_TABLES):
            match = re.search(rf"\b{re.escape(table)}\b", text)
            if match:
                findings.append(
                    {
                        "kind": "legacy_v2_table_in_init_schema",
                        "path": "migrations/0001_init.sql",
                        "line": _line_number(text, match.start()),
                        "match": table,
                    }
                )
    return findings


def run(root: Path) -> dict[str, Any]:
    root = root.resolve()
    findings = (
        _check_migrations(root)
        + _scan_text(root)
        + _check_active_surface(root)
        + _check_bootstrap_contract(root)
        + _check_status_contract(root)
        + _check_config_contract(root)
        + _check_removed_legacy_modules(root)
    )
    return {
        "status": "failed" if findings else "ok",
        "counts": {"findings": len(findings)},
        "findings": findings,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = run(Path(args.root))
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"status={payload['status']} findings={payload['counts']['findings']}")
        for item in payload["findings"]:
            print(f"{item['kind']} {item['path']}:{item['line']} {item['match']}")
    return 1 if payload["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
