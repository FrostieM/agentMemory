"""Run performance benchmarks for memory trust operations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.embeddings.factory import get_embedding_provider
from agent_memory_lite.maintenance.benchmark import run_memory_benchmarks
from agent_memory_lite.vector_store.factory import get_vector_store


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark memory trust operations.")
    parser.add_argument("--workspace", "--workspace-id", dest="workspace", default=None)
    parser.add_argument("--db-path", "--db", dest="db_path", default=None)
    parser.add_argument("--vector-path", "--vectors", dest="vector_path", default=None)
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--max-brief-tokens", type=int, default=2500)
    parser.add_argument("--with-vector", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--max-memory-search-ms", type=float, default=None)
    parser.add_argument("--max-memory-brief-ms", type=float, default=None)
    parser.add_argument("--max-fts-search-ms", type=float, default=None)
    parser.add_argument("--max-integrity-audit-ms", type=float, default=None)
    parser.add_argument("--max-hygiene-report-ms", type=float, default=None)
    parser.add_argument("--max-quality-gate-ms", type=float, default=None)
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=None)
    updates: dict[str, Any] = {}
    if args.workspace:
        updates["workspace_id"] = args.workspace
    if args.db_path:
        updates["db_path"] = Path(args.db_path)
    if args.vector_path:
        updates["vector_db_path"] = Path(args.vector_path)
    return settings.model_copy(update=updates)


def _thresholds(args: argparse.Namespace) -> dict[str, float]:
    thresholds: dict[str, float] = {}
    if args.max_memory_search_ms is not None:
        thresholds["memory_search"] = args.max_memory_search_ms
    if args.max_memory_brief_ms is not None:
        thresholds["memory_brief"] = args.max_memory_brief_ms
    if args.max_fts_search_ms is not None:
        thresholds["fts_search"] = args.max_fts_search_ms
    if args.max_integrity_audit_ms is not None:
        thresholds["integrity_audit"] = args.max_integrity_audit_ms
    if args.max_hygiene_report_ms is not None:
        thresholds["hygiene_report"] = args.max_hygiene_report_ms
    if args.max_quality_gate_ms is not None:
        thresholds["quality_gate"] = args.max_quality_gate_ms
    return thresholds


def _print(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"status={payload['status']} workspace_id={payload['workspace_id']}")
    for result in payload["results"]:
        if isinstance(result, dict):
            print(
                f"{result['name']}: p95={result['p95_ms']}ms "
                f"mean={result['mean_ms']}ms status={result['status']}"
            )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = _settings(args)
    conn = open_connection(settings.db_path)
    store = get_vector_store(settings) if args.with_vector else None
    provider = get_embedding_provider(settings) if args.with_vector else None
    try:
        report = run_memory_benchmarks(
            conn,
            workspace_id=settings.workspace_id,
            queries=args.query or ["memory trust benchmark", "workspace manifest"],
            runs=args.runs,
            embedding_provider=provider,
            vector_store=store,
            max_context_tokens=args.max_brief_tokens,
            thresholds_ms=_thresholds(args),
        )
        payload = report.to_dict()
        _print(payload, as_json=args.json)
    except Exception as exc:
        print(f"memory_benchmark_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()
        close_connection(conn)
    return 2 if report.status == "degraded" else 0


if __name__ == "__main__":
    raise SystemExit(main())
