from __future__ import annotations

import json
from pathlib import Path

from agent_memory_lite.maintenance.trends import build_trend_report


def test_trend_report_keeps_historical_degradation_visible_without_blocking_latest_ok(
    tmp_path: Path,
) -> None:
    audit_dir = tmp_path / "audit_runs"
    audit_dir.mkdir()
    (audit_dir / "001.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-01-01T00:00:00+00:00",
                "status": "degraded",
                "integrity": {"status": "degraded"},
                "retrieval_eval": {"status": "ok"},
                "hygiene": {"status": "ok"},
                "failures": ["broken"],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    (audit_dir / "002.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-01-02T00:00:00+00:00",
                "status": "ok",
                "integrity": {"status": "ok"},
                "retrieval_eval": {"status": "ok"},
                "hygiene": {"status": "ok"},
                "failures": [],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )

    report = build_trend_report(audit_dir)

    assert report.status == "ok"
    assert report.counts["degraded"] == 1
    assert report.warnings == []
