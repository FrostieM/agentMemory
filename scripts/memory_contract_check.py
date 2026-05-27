"""Check that agent-memory-lite operating contracts are present and generic."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from agent_memory_lite.config.settings import Settings

_DEFAULT_DOC_GLOBS = [
    "AGENTS.md",
    "CLAUDE.md",
    "README*.md",
    "docs/**/*.md",
    "AGENT_SETUP/**/*.md",
]
_DEFAULT_EXCLUDED_DOCS = {Path("docs/V3_1_BREAKTHROUGH_ROADMAP.md")}
_DEFAULT_EXCLUDED_PREFIXES = (Path("docs/adr"),)
_REQUIRED_CONTRACT_TOKENS = [
    "memory_brief",
    "memory_search",
    "memory_get",
    "memory_write",
    "memory_edit",
    "memory_impact_check",
    "memory_status",
    "memory_plan",
    "scripts/memory_audit.py",
    "scripts/memory_quality_gate.py",
    "scripts/memory_mcp_smoke.py",
    "scripts/memory_trust_dashboard.py",
    "MEMORY_STRICT_WORKSPACE_ISOLATION",
    "/ui",
    "/ui/recall",
    "/ui/metrics",
]
_DEFAULT_WORKSPACE_PATTERNS = [
    re.compile(r"workspace_id\s*=\s*['\"]default['\"]"),
    re.compile(r"['\"]workspace_id['\"]\s*:\s*['\"]default['\"]"),
    re.compile(r"--workspace(?:-id)?\s+default\b"),
]
_PROJECT_NAME_RE = re.compile(r"\bcopyBot\b", re.IGNORECASE)
_LEGACY_ALLOWED_CONTEXT_RE = re.compile(
    r"\b(?:"
    r"404|deleted|forbidden|no\s+longer|not\s+active|not\s+registered|"
    r"reject(?:ed)?|removed|unsupported|was\s+removed|were\s+removed|"
    r"do\s+not\s+use|must\s+not\s+use"
    r")\b",
    re.IGNORECASE,
)
_LEGACY_CONTRACT_PATTERNS = [
    ("legacy_mcp_tool", re.compile(r"\bmemory_get_context\b")),
    ("legacy_mcp_tool", re.compile(r"\bmemory_get_object\b")),
    ("legacy_mcp_tool", re.compile(r"\bmemory_list_[a-z_]+\b")),
    ("legacy_mcp_tool", re.compile(r"\bmemory_write_(?:decision|theory|task_state)\b")),
    ("legacy_mcp_tool", re.compile(r"\bmemory_record_with_evidence\b")),
    ("legacy_mcp_tool", re.compile(r"\bmemory_upsert_[a-z_]+\b")),
    ("legacy_mcp_tool", re.compile(r"\bmemory_(?:file_digest|find_symbols|graph_neighbors)\b")),
    ("legacy_mcp_tool", re.compile(r"\bmemory_code_(?:overview|graph)\b")),
    ("legacy_mcp_tool", re.compile(r"\bmemory_(?:claim_edit|release_edit)\b")),
    ("legacy_mcp_tool", re.compile(r"\bmemory_(?:ingest_episode|distill_insight)\b")),
    ("legacy_mcp_tool", re.compile(r"\bmemory_update_task_state\b")),
    ("legacy_http_route", re.compile(r"/memory/get_context\b")),
    ("legacy_http_route", re.compile(r"/memory/get_object\b")),
    ("legacy_http_route", re.compile(r"/memory/list_[A-Za-z_]+\b")),
    (
        "legacy_http_route",
        re.compile(
            r"/memory/(?:"
            r"decision_candidates|insight_candidates|snapshot_(?:save|list|diff)|"
            r"write_decision|write_theory|record_with_evidence|"
            r"upsert_[A-Za-z_]+|update_task_state|file_digest|find_symbols|"
            r"graph_neighbors|code_overview|code_graph|claim_edit|release_edit"
            r")\b"
        ),
    ),
    ("legacy_storage_name", re.compile(r"\bdecision_candidates\b")),
    ("legacy_storage_name", re.compile(r"\binsight_candidates\b")),
    ("legacy_storage_name", re.compile(r"\bmemory_state_snapshots\b")),
    ("legacy_migration_path", re.compile(r"\bmigrations/canonical/")),
    ("legacy_migration_path", re.compile(r"\bscripts/migrate_to_canonical\.py\b")),
    ("legacy_migration_path", re.compile(r"\bscripts/migrate_behaviors_to_canonical\.py\b")),
]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check memory-agent contract docs.")
    parser.add_argument("--root", default=".", help="Project root to scan.")
    parser.add_argument("--workspace", "--workspace-id", dest="workspace", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--strict",
        "--fail-on-warnings",
        dest="strict",
        action="store_true",
        help="Return non-zero when warnings are present.",
    )
    parser.add_argument(
        "--allow-project-name",
        action="append",
        default=[],
        help="Project-specific token allowed in generic docs. Repeatable.",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="Explicit file or directory to scan. Default scans known doc paths.",
    )
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=None)
    updates: dict[str, Any] = {}
    if args.workspace:
        updates["workspace_id"] = args.workspace
    return settings.model_copy(update=updates)


def _iter_default_files(root: Path) -> list[Path]:
    files: dict[str, Path] = {}
    for pattern in _DEFAULT_DOC_GLOBS:
        for path in root.glob(pattern):
            rel = path.relative_to(root) if path.is_relative_to(root) else path
            excluded_prefix = any(
                rel == prefix or prefix in rel.parents for prefix in _DEFAULT_EXCLUDED_PREFIXES
            )
            if path.is_file() and rel not in _DEFAULT_EXCLUDED_DOCS and not excluded_prefix:
                files[str(path.resolve())] = path
    return sorted(files.values())


def _iter_explicit_paths(root: Path, paths: list[str]) -> list[Path]:
    files: dict[str, Path] = {}
    for raw in paths:
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
        if path.is_file():
            files[str(path.resolve())] = path
        elif path.is_dir():
            for item in path.rglob("*.md"):
                if item.is_file():
                    files[str(item.resolve())] = item
    return sorted(files.values())


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _line_at(text: str, offset: int) -> str:
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    if end == -1:
        end = len(text)
    return text[start:end]


def _scan_file(path: Path, root: Path, allowed_project_names: set[str]) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    findings: list[dict[str, Any]] = []
    allowed_name_tokens = {item.lower() for item in allowed_project_names if item}
    for pattern in _DEFAULT_WORKSPACE_PATTERNS:
        for match in pattern.finditer(text):
            line_text = _line_at(text, match.start()).lower()
            if any(token in line_text for token in allowed_name_tokens):
                continue
            findings.append(
                {
                    "kind": "hard_coded_default_workspace",
                    "severity": "warning",
                    "path": rel,
                    "line": _line_number(text, match.start()),
                    "match": match.group(0),
                    "summary": "Generic memory docs should use <workspace_id>, not default.",
                }
            )
    if "copybot" not in allowed_name_tokens:
        for match in _PROJECT_NAME_RE.finditer(text):
            findings.append(
                {
                    "kind": "project_name_in_generic_docs",
                    "severity": "warning",
                    "path": rel,
                    "line": _line_number(text, match.start()),
                    "match": match.group(0),
                    "summary": "Generic memory docs should not mention a specific project.",
                }
            )
    for line_number, line_text in enumerate(text.splitlines(), start=1):
        if _LEGACY_ALLOWED_CONTEXT_RE.search(line_text):
            continue
        for kind, pattern in _LEGACY_CONTRACT_PATTERNS:
            for match in pattern.finditer(line_text):
                findings.append(
                    {
                        "kind": kind,
                        "severity": "error",
                        "path": rel,
                        "line": line_number,
                        "match": match.group(0),
                        "summary": "Active contract docs must use the canonical v3 surface only.",
                    }
                )
    return findings


def run_contract_check(
    *,
    root: Path,
    workspace_id: str,
    explicit_paths: list[str],
    allowed_project_names: set[str],
) -> dict[str, Any]:
    root = root.resolve()
    files = (
        _iter_explicit_paths(root, explicit_paths) if explicit_paths else _iter_default_files(root)
    )
    findings: list[dict[str, Any]] = []
    combined_text_parts: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            findings.append(
                {
                    "kind": "unreadable_contract_file",
                    "severity": "error",
                    "path": str(path),
                    "summary": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        combined_text_parts.append(text)
        findings.extend(_scan_file(path, root, allowed_project_names))

    combined_text = "\n".join(combined_text_parts)
    missing_tokens = [token for token in _REQUIRED_CONTRACT_TOKENS if token not in combined_text]
    for token in missing_tokens:
        findings.append(
            {
                "kind": "missing_contract_token",
                "severity": "warning",
                "path": str(root),
                "summary": f"Contract docs do not mention {token}.",
            }
        )
    if "agent-memory-lite-contract:begin" not in combined_text:
        findings.append(
            {
                "kind": "missing_contract_block",
                "severity": "warning",
                "path": str(root),
                "summary": "No agent-memory-lite contract marker found in scanned docs.",
            }
        )

    failures = [item["summary"] for item in findings if item["severity"] == "error"]
    warnings = [item["summary"] for item in findings if item["severity"] == "warning"]
    status = "degraded" if failures else ("warning" if warnings else "ok")
    return {
        "status": status,
        "workspace_id": workspace_id,
        "root": str(root),
        "files_scanned": [
            str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
            for path in files
        ],
        "counts": {
            "files": len(files),
            "findings": len(findings),
            "warnings": len(warnings),
            "failures": len(failures),
        },
        "findings": findings,
        "warnings": warnings,
        "failures": failures,
    }


def _print(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"status={payload['status']} workspace_id={payload['workspace_id']}")
    print("counts=" + json.dumps(payload["counts"], ensure_ascii=False, sort_keys=True))
    for finding in payload["findings"]:
        print(
            f"{finding['severity']} {finding['kind']} {finding.get('path')}:{finding.get('line', '-')}"
            f" - {finding['summary']}"
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = _settings(args)
    payload = run_contract_check(
        root=Path(args.root),
        workspace_id=settings.workspace_id,
        explicit_paths=args.path,
        allowed_project_names={str(item) for item in args.allow_project_name},
    )
    _print(payload, as_json=args.json)
    if payload["status"] == "degraded":
        return 2
    if args.strict and payload["status"] == "warning":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
